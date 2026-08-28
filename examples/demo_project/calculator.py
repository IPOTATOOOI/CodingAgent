def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        raise ValueError("Cannot divide by zero")
