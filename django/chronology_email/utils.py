from datetime import datetime

import extract_msg


def extract_email_details(file_path):
    msg = extract_msg.Message(file_path)
    msg_date = msg.date

    if isinstance(msg_date, str):
        msg_date = datetime.strptime(msg_date, "%a, %d %b %Y %H:%M:%S %z")

    body = msg.body or ""
    preview_context = (
        " ".join(body.split(". ")[:2]) + "..." if body else "No content available"
    )

    attachment_count = len(msg.attachments)

    return {
        "sender": msg.sender,
        "receiver": msg.to,
        "sent_date": msg_date,
        "preview_context": preview_context,
        "attachment_count": attachment_count,
    }
