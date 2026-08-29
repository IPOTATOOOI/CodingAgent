from text_utils import slugify


assert slugify("Hello World") == "hello-world"
assert slugify("  Mini Agent  ") == "mini-agent"
