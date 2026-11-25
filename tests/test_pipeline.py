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

    def test_media_rules(self):
        from pipeline import media
        self.assertIsNotNone(media.license_rank("CC BY 3.0"))
        self.assertIsNotNone(media.license_rank("CC BY-SA 4.0"))
        self.assertIsNotNone(media.license_rank("CC0"))
        self.assertIsNone(media.license_rank("CC BY-NC 4.0"))
        self.assertIsNone(media.license_rank("CC BY-ND 2.0"))
        self.assertIsNone(media.license_rank("Fair use"))
        self.assertTrue(media.name_in_title("File:20250310 Cha Eunwoo 차은우 01.jpg", ["Cha Eun-woo", "차은우"]))
        self.assertFalse(media.name_in_title("File:Seoul street.jpg", ["Cha Eun-woo", "차은우"]))

    def test_assign_media(self):
        from pipeline import media, shorts
        ph = [media.Media("photo", Path(f"p{i}"), f"t{i}", "", "CC BY 3.0", "", "") for i in range(2)]
        cl = media.Media("clip", Path("c"), "c", "", "CC BY 3.0", "", "")
        self.assertEqual(shorts.assign_media([]), [None] * 5)
        out = shorts.assign_media(ph + [cl])
        self.assertEqual(out[0].kind, "photo")
        self.assertEqual(out[1].kind, "clip")
        self.assertTrue(all(m.kind == "photo" for m in out[2:]))

    def test_band_rules(self):
        story = write.FakeWriter().write({}, "", "연예")
        story["shorts"]["band"] = {"text": "아주 긴 고정 제목이 열여덟 자를 훌쩍 넘어감", "hot": "없는말"}
        probs = " ".join(qa.check(story, self.cfg))
        self.assertIn("band", probs)

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


    def test_shorts_motion_falls_back_to_cards(self):
        """새 방식이 실패해도 기존 카드 방식으로 영상이 만들어진다."""
        from unittest import mock
        from pipeline import shorts
        news.store(self.conn, news.parse(RSS.encode(), "entertainment"))
        self.conn.execute("UPDATE candidates SET published_at = ?", (db.now_iso(),))
        stories.run(self.conn, self.cfg, write.FakeWriter(), {})
        story = shorts.plan(self.conn, self.cfg)[0]
        self.cfg["shorts"].update(renderer="motion", tts_provider="silent", media=False)
        with tempfile.TemporaryDirectory() as d,                 mock.patch.object(shorts, "_motion", side_effect=RuntimeError("no node")):
            ok, probs = shorts.produce(self.conn, self.cfg, story, Path(d), writer=write.FakeWriter())
            self.assertTrue(ok, probs)
            self.assertTrue((Path(d) / f"{story['slug']}.mp4").exists())
        v = json.loads(self.conn.execute("SELECT variant FROM videos").fetchone()[0])
        self.assertEqual(v["layout"], "frame")

    def test_learning_loop(self):
        """성과 → 상대 점수 → 테마 가중치(0.5~2.0, 표본 3개 미만은 1.0) → 지침."""
        from unittest import mock
        from pipeline import learn, metrics
        metrics.ensure(self.conn)
        old = "2026-01-01T00:00:00+00:00"
        cats = ["연예"] * 4 + ["꿀팁"] * 4
        for i, cat in enumerate(cats):
            slug = f"s{i}"
            theme = "neon" if i < 4 else "mint"
            self.conn.execute("INSERT INTO videos (slug, title, description, tags, seconds, status, youtube_id, variant, created_at, updated_at) "
                              "VALUES (?, ?, '', '[]', 50, 'uploaded', ?, ?, ?, ?)",
                              (slug, f"제목{i}", f"yt{i}", json.dumps({"theme": theme, "layout": "motion", "photos": i % 2}), old, old))
            metrics.save(self.conn, slug, "youtube", views=1000 if i >= 4 else 100, retention=80)
        # 분야·화면 구성은 _features 를 흉내 낸다 (stories 표는 컬럼이 많아 직접 넣지 않음)
        feats = {f"s{i}": {"title": f"제목{i}", "category": c, "theme": "neon" if i < 4 else "mint",
                           "layout": "motion", "visual": "photo" if i % 2 else "nophoto"} for i, c in enumerate(cats)}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(learn, "PLAYBOOK", Path(d) / "pb.json"), \
                mock.patch.object(learn, "_features", return_value=feats):
            res = learn.update(self.conn, writer=None)
            self.assertEqual(res["scored"], 8)
            w = res["theme_weights"]
            self.assertGreater(w["mint"], 1.0)
            self.assertLess(w["neon"], 1.0)
            self.assertTrue(all(learn.W_MIN <= x <= learn.W_MAX for x in w.values()))
            self.assertEqual(w["paper"], 1.0)          # 표본 없음 → 계속 시험
            g = learn.guidance(learn.load(), "pick")
            self.assertIn("꿀팁", g)
            self.assertIn("항상 우선", g)
            self.assertEqual(learn.guidance(learn.load(), "write"), "")   # 교훈이 아직 없음

    def test_pick_layout_follows_weights(self):
        from pipeline import shorts
        slugs = [f"20261125-{i:04x}" for i in range(400)]
        even = sum(shorts.pick_layout(x) == "motion" for x in slugs) / 400
        skew = sum(shorts.pick_layout(x, {"motion": 0.5, "frame": 1.5}) == "motion" for x in slugs) / 400
        self.assertTrue(0.4 < even < 0.6, even)
        self.assertTrue(0.15 < skew < 0.35, skew)
        self.assertEqual(shorts.pick_layout(slugs[0]), shorts.pick_layout(slugs[0]))

    def test_person_photo_matches_scene(self):
        """장면 대본의 인물(who) 사진만 넣는다. 그 인물 사진이 없으면 다른 출연자 사진 대신 이모지."""
        from unittest import mock
        from pipeline import media, visuals
        people = [{"name_ko": "김종국", "name_en": "Kim Jong-kook", "aliases": []},
                  {"name_ko": "안재현", "name_en": "Ahn Jae-hyun", "aliases": []}]
        self.assertIs(visuals.match_person("Kim Jong-kook", people), people[0])
        self.assertIsNone(visuals.match_person("", people))
        self.assertIs(visuals.match_person("", people[:1]), people[0])
        only_ahn = [media.Media("photo", Path("a.jpg"), "File:Ahn.png", "HB", "CC BY 3.0", "", "안재현")]
        plan = {"scenes": [{"scene": "hook", "kind": "person", "who": "김종국", "emoji": ""},
                           {"scene": "reaction", "kind": "person", "who": "안재현", "emoji": ""}]}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(media, "gather", return_value=only_ahn):
            out = visuals.resolve(plan, people, Path(d))
        self.assertEqual(out[0]["kind"], "emoji")
        self.assertEqual(out[1]["kind"], "person")
        self.assertEqual(out[1]["title"], "File:Ahn.png")


if __name__ == "__main__":
    unittest.main()
