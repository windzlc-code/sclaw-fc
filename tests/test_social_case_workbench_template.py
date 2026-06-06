import unittest
from pathlib import Path


class SocialCaseWorkbenchTemplateTests(unittest.TestCase):
    def test_workbench_explains_case_completeness_requirements_and_flow(self):
        html = Path("templates/social_case_workbench.html").read_text(encoding="utf-8")

        required_text = [
            "案件完整度要求",
            "上線前門檻",
            "quality-gate-strip",
            "quality-gate-chip",
            ".guide-steps {\n        grid-auto-flow: column",
            "資料→圖片→稿件→影片→發布",
            "資料補齊",
            "圖片檢查",
            "文案輸出",
            "視頻生成",
            "發布回查",
            "完整度低於 65",
            "圖片少於 3 張",
            "caseVideoGate",
            "caseOutputStripHtml",
        ]

        for text in required_text:
            with self.subTest(text=text):
                self.assertIn(text, html)

        removed_long_copy = [
            "先把案件資料補齊、圖片過濾乾淨，再下載小紅書圖文稿、口播稿與素材包，最後生成視頻與發布。",
            "工作台以「資料補齊 → 圖片檢查 → 文案輸出 → 視頻生成 → 發布回查」作為固定順序；每個案件卡片會顯示完整度、缺漏欄位、圖片數與可輸出項目。",
        ]
        for text in removed_long_copy:
            with self.subTest(removed=text):
                self.assertNotIn(text, html)

    def test_home_ai_summary_renders_social_digest_reading_modes(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")
        css = Path("static/site.css").read_text(encoding="utf-8")

        required_html = [
            "dialogKnowledgeDigestHtml",
            "近 15 天最新資料",
            "社媒影片/圖文優先",
            "reading_card",
            "primary_action",
            "summary_points",
            "needs_bootstrap",
        ]
        required_css = [
            ".dialog-knowledge-digest",
            ".dialog-digest-platform-logo",
            ".dialog-digest-item-points",
        ]

        for text in required_html:
            with self.subTest(text=text):
                self.assertIn(text, html)
        for text in required_css:
            with self.subTest(text=text):
                self.assertIn(text, css)


if __name__ == "__main__":
    unittest.main()
