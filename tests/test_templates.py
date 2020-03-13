import pytest

from ocdeployer.templates import Template


@pytest.mark.parametrize(
    "value,expected",
    (
        (True, "true"),
        ("True", "True"),
        ("true", "true"),
        ("123", "123"),
        (123, "123"),
        ("123:123:123", "123:123:123"),
        ("some text", "some text"),
    ),
)
def test_template_oc_param_format(value, expected):
    assert Template._format_oc_parameter(value) == expected
