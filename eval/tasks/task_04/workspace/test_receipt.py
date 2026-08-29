from receipt import receipt_total


assert receipt_total([50, 30, 20], 0.1) == 90
assert receipt_total([10], 0.0) == 10
