"""The CORS allowlist is a security boundary, so its edges get pinned down."""

import re

import pytest

from app.config import ALLOWED_ORIGIN_REGEX, ALLOWED_ORIGINS


@pytest.fixture
def origin_pattern():
    return re.compile(ALLOWED_ORIGIN_REGEX)


class TestAllowedOriginRegex:
    @pytest.mark.parametrize(
        "origin",
        [
            "https://opensquawk.de",
            "https://www.opensquawk.de",
            "https://app.opensquawk.de",
            "https://staging.app.opensquawk.de",
        ],
    )
    def test_accepts_our_own_https_subdomains(self, origin_pattern, origin):
        assert origin_pattern.match(origin)

    @pytest.mark.parametrize(
        "origin",
        [
            # Without anchors these three all match a naive "opensquawk.de"
            # pattern — which is how a CORS allowlist quietly stops being one.
            "https://evil-opensquawk.de",
            "https://opensquawk.de.attacker.com",
            "https://attacker.com/?x=https://app.opensquawk.de",
            # Plain http would let a network attacker read authenticated
            # responses; the deployment is https-only.
            "http://app.opensquawk.de",
        ],
    )
    def test_rejects_lookalikes_and_plain_http(self, origin_pattern, origin):
        assert not origin_pattern.match(origin)

    def test_localhost_still_comes_from_the_explicit_list(self):
        # Development origins are not https and not on our domain, so the regex
        # cannot cover them — losing them would break every local frontend.
        assert "http://localhost:3000" in ALLOWED_ORIGINS
        assert "http://127.0.0.1:3000" in ALLOWED_ORIGINS
