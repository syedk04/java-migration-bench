"""Tests for S17: network monitor."""

from migration_agent.network_monitor import _APPROVED_HOSTS, parse_download_urls


def test_parse_download_urls_basic():
    output = """
[INFO] Downloading from central: https://repo1.maven.org/maven2/org/springframework/spring-core/5.3.0/spring-core-5.3.0.pom
[INFO] Downloaded from central: https://repo1.maven.org/maven2/org/springframework/spring-core/5.3.0/spring-core-5.3.0.pom
"""
    urls = parse_download_urls(output)
    assert len(urls) == 1  # deduped: Downloading and Downloaded are same URL
    assert urls[0]["host"] == "repo1.maven.org"
    assert urls[0]["approved"] is True


def test_parse_download_urls_unapproved():
    output = "Downloading from jitpack: https://jitpack.io/com/example/lib/1.0/lib-1.0.jar"
    urls = parse_download_urls(output)
    assert len(urls) == 1
    assert urls[0]["approved"] is False
    assert urls[0]["host"] == "jitpack.io"


def test_parse_download_urls_empty():
    assert parse_download_urls("BUILD SUCCESS") == []


def test_parse_download_urls_dedup():
    url = "https://repo1.maven.org/maven2/artifact.jar"
    output = f"Downloading: {url}\nDownloading: {url}"
    urls = parse_download_urls(output)
    assert len(urls) == 1


def test_approved_hosts_contains_central():
    assert "repo1.maven.org" in _APPROVED_HOSTS
    assert "central.maven.org" in _APPROVED_HOSTS


def test_parse_multiple_hosts():
    output = """
Downloading from central: https://repo1.maven.org/maven2/a.jar
Downloading from sonatype: https://oss.sonatype.org/content/repositories/releases/b.jar
Downloading from jitpack: https://jitpack.io/c.jar
"""
    urls = parse_download_urls(output)
    hosts = {u["host"] for u in urls}
    assert "repo1.maven.org" in hosts
    assert "oss.sonatype.org" in hosts
    assert "jitpack.io" in hosts
    approved = [u for u in urls if u["approved"]]
    unapproved = [u for u in urls if not u["approved"]]
    assert len(approved) == 2
    assert len(unapproved) == 1
