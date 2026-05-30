import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gamedesigner.pixel_site_downloader import (
    decide_candidate,
    extract_entry_urls,
    extract_image_urls,
    export_target_pngs,
    ImageInfo,
    inspect_image,
    is_same_host_page_url,
    raw_path_for_url,
)


class PixelSiteDownloaderTests(unittest.TestCase):
    def test_extracts_owned_blog_images_and_entry_links(self) -> None:
        html = """
        <a href="http://pndsndn.blog79.fc2.com/blog-entry-207.html">entry</a>
        <a href="https://static.fc2.com/logo.png">ignore chrome</a>
        <img src="https://blog-imgs-122.fc2.com/p/n/d/pndsndn/javelin_azrm_00.png">
        <a href="//blog-imgs-115.fc2.com/p/n/d/pndsndn/nicholas_azrn_8c_00.png">full</a>
        """

        images = extract_image_urls(html, "http://pndsndn.blog79.fc2.com/")
        entries = extract_entry_urls(html, "http://pndsndn.blog79.fc2.com/")

        self.assertEqual(len(images), 2)
        self.assertTrue(all("/p/n/d/pndsndn/" in item for item in images))
        self.assertEqual(entries, ["http://pndsndn.blog79.fc2.com/blog-entry-207.html"])

    def test_generic_page_crawler_accepts_same_host_pages_but_not_images(self) -> None:
        html = """
        <a href="https://artist.example.test/gallery">gallery</a>
        <a href="https://artist.example.test/gallery?page=2">paged</a>
        <a href="https://cdn.artist.example.test/hero.png">image</a>
        <a href="https://other.example.test/page">other</a>
        """

        entries = extract_entry_urls(html, "https://artist.example.test/", page_host="artist.example.test", generic_pages=True)

        self.assertEqual(entries, ["https://artist.example.test/gallery", "https://artist.example.test/gallery?page=2"])
        self.assertTrue(is_same_host_page_url("https://artist.example.test/gallery", page_host="artist.example.test"))
        self.assertFalse(is_same_host_page_url("https://artist.example.test/hero.png", page_host="artist.example.test"))

    def test_candidate_prefers_large_transparent_character_images(self) -> None:
        accepted = decide_candidate(
            Path("hero.png"),
            ImageInfo(width=128, height=160, mode="P", format="PNG", has_alpha=True),
            min_width=96,
            min_height=96,
            min_area=10_000,
            require_alpha=True,
        )
        rejected = decide_candidate(
            Path("icon.png"),
            ImageInfo(width=64, height=64, mode="P", format="PNG", has_alpha=True),
            min_width=96,
            min_height=96,
            min_area=10_000,
            require_alpha=True,
        )

        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.category, "character_portrait")
        self.assertFalse(rejected.accepted)

    def test_exports_target_png_from_transparent_raw(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw.png"
            Image.new("RGBA", (128, 160), (20, 40, 80, 0)).save(raw)

            info = inspect_image(raw)
            outputs = export_target_pngs(
                raw,
                target_dir=root / "targets",
                extract_gif_frames=True,
                max_gif_frames=8,
            )

            self.assertTrue(info.has_alpha)
            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].exists())

    def test_raw_paths_are_stable_and_extension_grouped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = raw_path_for_url(
                "https://blog-imgs-122.fc2.com/p/n/d/pndsndn/javelin_azrm_00.png",
                Path(folder),
            )
            second = raw_path_for_url(
                "https://blog-imgs-122.fc2.com/p/n/d/pndsndn/javelin_azrm_00.png",
                Path(folder),
            )

            self.assertEqual(first, second)
            self.assertEqual(first.parent.name, "png")
            self.assertEqual(first.suffix, ".png")


if __name__ == "__main__":
    unittest.main()
