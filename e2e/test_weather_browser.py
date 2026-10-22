"""Wetterseite und lazy Modellvergleich bei 390 px im bestehenden E2E-Setup."""
from playwright.sync_api import expect


def test_weather_mobile(angemeldete_seite, base_url):
    page = angemeldete_seite
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url + "/wetter")
    expect(page.locator('#wetter-map')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.get_by_text('Modellvergleich (6 Stunden)', exact=True).click()
    expect(page.locator('.weather-compare')).to_be_visible(timeout=30000)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
