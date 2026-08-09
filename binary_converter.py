import base64


def base64_check(data):
    try:
        return base64.b64encode(base64.b64decode(data)) == data
    except Exception:
        return False


def convert_binary(data, into: str):
    if into == "PIL":
        if type(data) == str:
            return base64.b64decode(data.encode())
        elif not base64_check(data):  # bytes but not in base 64
            return data
        else:
            return base64.b64decode(data)
    elif into == "binary":
        if type(data) == str:
            return data.encode()
        elif not base64_check(data):  # bytes but not in base 64
            return base64.b64encode(data)
        else:
            return data
    elif into == "string":
        if type(data) == str:
            return data
        elif not base64_check(data):  # bytes but not in base 64
            data = base64.b64encode(data)
        return data.decode()
    else:
        return data
