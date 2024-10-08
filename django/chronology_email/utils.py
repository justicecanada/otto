import pypff


def extract_email_date(file_path):
    msg_file = pypff.file()
    msg_file.open(file_path)

    properties = msg_file.get_message()
    date = properties.get_property(pypff.message.PROPERTY_TAG_MESSAGE_DELIVERY_TIME)

    return date
