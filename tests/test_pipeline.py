import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import config, db, news, qa, stories, write  # noqa: E402

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>배우 A, 촬영 중 부상 - 가신문</title><link>https://news.google.com/x1</link>
<pubDate>Mon, 16 Nov 2026 07:05:15 GMT</pubDate><source>가신문</source>
<description>&lt;ol&gt;&lt;li&gt;&lt;a href="https://n/1"&gt;배우 A, 촬영 중 부상&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;가신문&lt;/font&gt;&lt;/li&gt;&lt;li&gt;&lt;a href="https://n/2"&gt;A 소속사 "회복 중"&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;나일보&lt;/font&gt;&lt;/li&gt;&lt;/ol&gt;</description></item>
</channel></rss>"""


class T(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config()
        self.conn = db.connect(":memory:")

    def test_parse_rss(self):
        items = news.parse(RSS.encode(), "entertainment")
        self.assertEqual(items[0]["title"], "배우 A, 촬영 중 부상")
        self.assertEqual(items[0]["heat"], 2)
        self.assertEqual(items[0]["related"][0]["outlet"], "나일보")

    def test_fake_story_passes_rules(self):
        story = write.FakeWriter().write({}, "", "연예")
        self.assertEqual(qa.check(story, self.cfg), [])

    def test_rules_catch_problems(self):
        story = write.FakeWriter().write({}, "", "연예")
        story["sources"] = story["sources"][:1]
        story["body_md"] += "\n\n그는 폭행을 저질렀다."
        story["title"] = "사기꾼 " + story["title"]
        probs = " ".join(qa.check(story, self.cfg))
        self.assertIn("출처 부족", probs)
        self.assertIn("단정", probs)
        self.assertIn("사기꾼", probs)

    def test_hedged_allegation_ok(self):
        story = write.FakeWriter().write({}, "", "연예")
        story["body_md"] += "\n\n가신문은 그가 폭행을 저질렀다는 의혹을 보도했다."
        self.assertEqual(qa.check(story, self.cfg), [])

    def test_quote_pairs(self):
        story = write.FakeWriter().write({}, "", "연예")
        story["body_md"] += '\n\n그는 "괜찮다"고 했습니다. ' + "가" * 80 + ' 소속사는 "회복 중"이라고 밝혔습니다.'
        self.assertEqual(qa.check(story, self.cfg), [])
        story["body_md"] += '\n\n"' + "나" * 80 + '"'
        self.assertTrue(any("인용" in x for x in qa.check(story, self.cfg)))

    def test_instagram_caption(self):
        from pipeline import instagram
        cap = instagram.caption_for({"title": "제목 #Shorts", "tags": '["연예"]'}, "https://x/p/")
        self.assertTrue(cap.startswith("제목\n"))
        self.assertIn("#연예", cap)

    def test_end_to_end_fake(self):
        news.store(self.conn, news.parse(RSS.encode(), "entertainment"))
        self.conn.execute("UPDATE candidates SET published_at = ?", (db.now_iso(),))
        summary = {}
        stories.run(self.conn, self.cfg, write.FakeWriter(), summary)
        self.assertEqual(len(summary["published"]), 1)
        summary = {}
        stories.run(self.conn, self.cfg, write.FakeWriter(), summary)   # 같은 후보는 다시 쓰지 않음
        self.assertEqual(summary["published"], [])

    def test_review_rejection_not_published(self):
        news.store(self.conn, news.parse(RSS.encode(), "entertainment"))
        self.conn.execute("UPDATE candidates SET published_at = ?", (db.now_iso(),))
        summary = {}
        stories.run(self.conn, self.cfg, write.FakeWriter(approve=False), summary)
        self.assertEqual(summary["published"], [])
        self.assertEqual(len(summary["rejected"]), 1)

    def test_site_build_and_withdraw(self):
        from pipeline import run, site
        news.store(self.conn, news.parse(RSS.encode(), "entertainment"))
        self.conn.execute("UPDATE candidates SET published_at = ?", (db.now_iso(),))
        summary = {}
        stories.run(self.conn, self.cfg, write.FakeWriter(), summary)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "public"
            self.assertEqual(site.build(self.conn, self.cfg, out), 1)
            page = (out / "posts" / summary["published"][0]["slug"] / "index.html").read_text(encoding="utf-8")
            self.assertIn("출처", page)
            self.assertIn("나일보", page)
            run.withdraw(self.conn, summary["published"][0]["slug"])
            self.assertEqual(site.build(self.conn, self.cfg, out), 0)


if __name__ == "__main__":
    unittest.main()
