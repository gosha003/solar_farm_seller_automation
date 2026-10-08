from solar_outreach.sources import robots_allows, robots_rules

ALO = """User-agent: Googlebot
Disallow: /private
User-agent: *
Disallow: /searchc.php*
Disallow: /*?ajax=*
Disallow: /searchq/*page=
Allow: /
"""
BAZAR = """User-agent: *
Allow: /
Disallow: /обяви/
Disallow: /obiavi/*/oblast-varna
"""
BP = """User-agent: *
Disallow: /*search
Disallow: *ID=
Disallow: /*.pdf$
"""


def test_alo_rules():
    r = robots_rules(ALO)
    assert (False, "/private") not in r                    # чужая группа
    assert robots_allows(r, "/searchq/?q=фец")
    assert not robots_allows(r, "/searchq/?q=фец&page=2")
    assert not robots_allows(r, "/ad-123?ajax=1")
    assert robots_allows(r, "/fotovoltaichna-centrala-1-5mw-10983881")


def test_bazar_and_wildcards():
    r = robots_rules(BAZAR)
    assert robots_allows(r, "/obiavi?q=фец")
    assert not robots_allows(r, "/обяви/нещо")
    assert not robots_allows(r, "/obiavi/imoti/oblast-varna")
    b = robots_rules(BP)
    assert not robots_allows(b, "/bg/search?x=1")
    assert not robots_allows(b, "/show.php?ID=5")
    assert not robots_allows(b, "/doc.pdf") and robots_allows(b, "/doc.pdf.html")
    assert robots_allows(robots_rules(""), "/anything")


def test_multiple_user_agents_share_group():
    r = robots_rules("User-agent: bot1\nUser-agent: *\nDisallow: /x\n")
    assert not robots_allows(r, "/x/y")
