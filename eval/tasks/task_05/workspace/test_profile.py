from profile import create_profile
from validators import is_valid_username


assert is_valid_username("agent_7")
assert not is_valid_username("a b")
assert create_profile(" agent_7 ") == {"username": "agent_7"}
try:
    create_profile("a b")
except ValueError:
    pass
else:
    raise AssertionError("invalid username must be rejected")
