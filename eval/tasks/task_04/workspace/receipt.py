from discount import apply_discount


def receipt_total(prices, discount_rate):
    subtotal = sum(prices[:-1])
    return apply_discount(subtotal, discount_rate)
