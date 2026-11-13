import functools
import json
import os
import time

import pythoncom
import win32com.client
from dotenv import load_dotenv
from fastmcp import FastMCP

mcp = FastMCP("Outlook MCP Server")

script_path = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_path, ".env"))

OL_MAIL_ITEM = 0
OL_MAIL_CLASS = 43


def _safe(func):
    # Return errors as text: a raised exception makes the agent-side call hang.
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {type(exc).__name__}: {exc}"

    return wrapper


def _get_outlook():
    # Tools may run on worker threads; COM must be initialized per thread.
    pythoncom.CoInitialize()
    outlook = win32com.client.Dispatch("Outlook.Application")
    return outlook, outlook.GetNamespace("MAPI")


def _get_folder_by_path(namespace, folder_path: str):
    current = namespace.DefaultStore.GetRootFolder()
    parts = [p for p in folder_path.replace("/", "\\").split("\\") if p]
    if not parts:
        raise ValueError("folder_path must not be empty.")
    for part in parts:
        for sub in current.Folders:
            if sub.Name == part:
                current = sub
                break
        else:
            available = ", ".join(sub.Name for sub in current.Folders)
            raise ValueError(f"Folder '{part}' of '{folder_path}' was not found. Available here: {available}")
    return current


def _format_recipient(recipient) -> dict:
    try:
        address = recipient.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x39FE001E")
    except Exception:
        address = recipient.Address
    return {"name": recipient.Name, "email": address}


def _get_email_details(mail_item) -> dict:
    importance_map = {0: "Low", 1: "Normal", 2: "High"}
    sensitivity_map = {0: "Normal", 1: "Personal", 2: "Private", 3: "Confidential"}
    flag_status_map = {0: "NotFlagged", 1: "Completed", 2: "FlaggedForFollowUp"}

    sender_name = "N/A"
    sender_email = "N/A"
    try:
        sender_name = mail_item.SenderName
        sender_email = mail_item.SenderEmailAddress
        if mail_item.SenderEmailType == "EX":
            sender_email = mail_item.Sender.GetExchangeUser().PrimarySmtpAddress
    except Exception:
        pass

    categories = getattr(mail_item, "Categories", "")
    flag_due = getattr(mail_item, "FlagDueBy", None)
    details = {
        "entry_id": getattr(mail_item, "EntryID", None),
        "conversation_id": getattr(mail_item, "ConversationID", None),
        "subject": getattr(mail_item, "Subject", "N/A"),
        "sent_datetime": mail_item.SentOn.isoformat() if getattr(mail_item, "SentOn", None) else None,
        "received_datetime": mail_item.ReceivedTime.isoformat() if getattr(mail_item, "ReceivedTime", None) else None,
        "sender": {"name": sender_name, "email": sender_email},
        "recipients_to": [_format_recipient(r) for r in mail_item.Recipients if r.Type == 1],
        "recipients_cc": [_format_recipient(r) for r in mail_item.Recipients if r.Type == 2],
        "recipients_bcc": [_format_recipient(r) for r in mail_item.Recipients if r.Type == 3],
        "body_plain": getattr(mail_item, "Body", ""),
        "attachments": [
            {"filename": getattr(a, "FileName", "N/A"), "size_bytes": getattr(a, "Size", 0)}
            for a in mail_item.Attachments
        ],
        "categories": categories.split(";") if categories else [],
        "importance": importance_map.get(getattr(mail_item, "Importance", 1), "Normal"),
        "sensitivity": sensitivity_map.get(getattr(mail_item, "Sensitivity", 0), "Normal"),
        "flag_status": {
            "status": flag_status_map.get(getattr(mail_item, "FlagStatus", 0), "NotFlagged"),
            "due_date": flag_due.isoformat() if flag_due and flag_due.year > 1900 else None,
        },
    }
    return details


@mcp.tool()
@_safe
def send_outlook_email(
    to_recipients: str,
    subject: str,
    body: str,
    cc_recipients: str | None = None,
    bcc_recipients: str | None = None,
    attachments: list[str] | None = None,
    display_before_sending: bool = True,
) -> str:
    """
    Create an email in the local Outlook app and either show it for review or send it directly.

    Args:
        to_recipients: Recipient addresses, separated by semicolons.
        subject: Email subject.
        body: Email body (HTML or plain text).
        cc_recipients: CC addresses, separated by semicolons. Optional.
        bcc_recipients: BCC addresses, separated by semicolons. Optional.
        attachments: Absolute file paths to attach. Optional.
        display_before_sending: True (default) opens the draft window for the user to review and send manually;
            False sends immediately. Only use False when the user explicitly asks to send directly.

    Returns:
        A result message.
    """
    missing = [p for p in (attachments or []) if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError(f"Attachment(s) not found: {', '.join(missing)}")

    outlook, _ = _get_outlook()
    mail_item = outlook.CreateItem(OL_MAIL_ITEM)
    mail_item.To = to_recipients
    mail_item.Subject = subject
    mail_item.HTMLBody = body
    if cc_recipients:
        mail_item.CC = cc_recipients
    if bcc_recipients:
        mail_item.BCC = bcc_recipients
    for path in attachments or []:
        mail_item.Attachments.Add(os.path.abspath(path))

    if display_before_sending:
        mail_item.Display()
        return "The draft has been opened in Outlook. Please review and send it manually."
    mail_item.Send()
    return "The email has been sent."


@mcp.tool()
@_safe
def download_latest_email_data(folder_path: str, max_count: int = 50) -> str:
    """
    Export the latest emails of an Outlook folder (newest first) to a JSON file.

    Args:
        folder_path: Folder path under the mailbox root, separated by "/", e.g. "Inbox" or "Inbox/Reports".
            Use the folder names as shown in Outlook (they may be localized, e.g. "受信トレイ").
        max_count: Maximum number of emails to export. Defaults to 50.

    Returns:
        The saved JSON file path and the number of exported emails. Each email includes subject, sender,
        recipients, received/sent time, plain-text body and attachment names.
    """
    _, namespace = _get_outlook()
    folder = _get_folder_by_path(namespace, folder_path)
    # folder.Items returns a new collection each access, so sort and iterate the same instance.
    items = folder.Items
    items.Sort("[ReceivedTime]", True)

    emails = []
    for mail in items:
        if len(emails) >= max_count:
            break
        try:
            if mail.Class == OL_MAIL_CLASS:
                emails.append(_get_email_details(mail))
        except Exception:
            continue

    save_dir = os.path.join(script_path, "tmp")
    os.makedirs(save_dir, exist_ok=True)
    save_file = os.path.join(save_dir, f"{int(time.time() * 1000)}.json")
    with open(save_file, mode="w", encoding="utf-8") as f:
        json.dump(emails, f, ensure_ascii=False, indent=2)
    return json.dumps({"save_file": save_file.replace("\\", "/"), "count": len(emails)}, ensure_ascii=False)


if __name__ == "__main__":
    import sys

    sys.stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)

    mcp.run(transport="stdio")
