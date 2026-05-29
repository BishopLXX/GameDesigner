import unittest

from gamedesigner.pixel_refiner_open_assets import (
    extract_freegamesprites_image_urls,
    is_character_like_slug,
    is_asset_page,
    is_same_category_page,
    target_category_for_site_category,
)


class PixelRefinerOpenAssetsTests(unittest.TestCase):
    def test_extracts_freegamesprites_generated_webp_urls(self) -> None:
        html = """
        <img src="https://freegamesprites.com/images/ai-sprites/generated/ogre.webp">
        <img src="https://freegamesprites.com/images/ai-sprites/generated/ogre.webp">
        <img src="/images/chrome/logo.png">
        """

        urls = extract_freegamesprites_image_urls(html)

        self.assertEqual(urls, ["https://freegamesprites.com/images/ai-sprites/generated/ogre.webp"])

    def test_recognizes_asset_and_category_pages(self) -> None:
        self.assertTrue(is_asset_page("https://freegamesprites.com/en/assets/ogre"))
        self.assertFalse(is_asset_page("https://freegamesprites.com/en/category/characters"))
        self.assertTrue(is_same_category_page("https://freegamesprites.com/en/category/characters?page=2", "characters"))

    def test_maps_categories_to_training_categories(self) -> None:
        self.assertEqual(target_category_for_site_category("characters"), "character_portrait")
        self.assertEqual(target_category_for_site_category("enemies"), "side_scroller_action_character")

    def test_character_slug_filter_keeps_creatures_and_rejects_props(self) -> None:
        self.assertTrue(is_character_like_slug("animated-suit-of-armor-dark"))
        self.assertTrue(is_character_like_slug("goblin-raider-bow-wood"))
        self.assertTrue(is_character_like_slug("giant-spider-hairy-legs"))
        self.assertFalse(is_character_like_slug("castle-steward-pocket-watch"))
        self.assertFalse(is_character_like_slug("cursed-portal-purple"))
        self.assertFalse(is_character_like_slug("campfire-ember-burning"))
        self.assertFalse(is_character_like_slug("staff-druid-vfx"))
        self.assertFalse(is_character_like_slug("goblin-chief-lowres"))


if __name__ == "__main__":
    unittest.main()
