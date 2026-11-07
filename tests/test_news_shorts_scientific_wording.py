import unittest
from core.news_shorts_writer import _validate_scientific_wording


class ScientificScriptWordingTests(unittest.TestCase):
    def valid_script(self):
        return {"scenes": [{"say": "यह संभावित exposure है, बाढ़ का पूर्वानुमान नहीं। HAND का मतलब निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई है। नेपाल में 108,500 लोग 0–50 m HAND में और 22,800 लोग 0–10 m HAND में हैं। 293,800 लोग संभावित exposure में हैं। भवन क्षेत्रफल 46% बढ़ा और modelled GLOF extents में building footprint area 77% बढ़ा। अध्ययन यह स्थापित नहीं करता कि निर्माण ने बाढ़ कराई या उसकी संभावना बढ़ाई।",
                            "caption": "293,800; 108,500; 22,800; building footprint area 46%; GLOF building area 77%"}]}

    def test_accepts_correctly_qualified_study_wording(self):
        _validate_scientific_wording(self.valid_script())

    def test_rejects_horizontal_50m_radius_misinterpretation(self):
        script = self.valid_script()
        script["scenes"][0]["say"] += " यह नदी से 50 मीटर दायरे में है।"
        with self.assertRaisesRegex(ValueError, "unsupported causal"):
            _validate_scientific_wording(script)

    def test_rejects_causal_construction_claim(self):
        script = self.valid_script()
        script["scenes"][0]["say"] += " अनियंत्रित निर्माण से बाढ़ का खतरा बढ़ता है।"
        with self.assertRaisesRegex(ValueError, "unsupported causal"):
            _validate_scientific_wording(script)

    def test_rejects_vague_hand_definition(self):
        script = self.valid_script()
        script["scenes"][0]["say"] = script["scenes"][0]["say"].replace("निकटतम जलधारा से", "नदी से")
        with self.assertRaisesRegex(ValueError, "nearest drainage"):
            _validate_scientific_wording(script)

    def test_rejects_missing_flood_forecast_caveat(self):
        script = self.valid_script()
        script["scenes"][0]["say"] = "संभावित exposure है। HAND निकटतम जलधारा से ऊर्ध्वाधर ऊँचाई मापता है। भवन क्षेत्र बढ़ा।"
        with self.assertRaisesRegex(ValueError, "not a flood forecast"):
            _validate_scientific_wording(script)

    def test_rejects_missing_nepal_22800_figure(self):
        script = self.valid_script()
        script["scenes"][0]["say"] = script["scenes"][0]["say"].replace("22,800 लोग 0–10 m HAND में", "लोग 0–10 m HAND में")
        script["scenes"][0]["caption"] = "293,800; 108,500; building footprint area 46%; GLOF building area 77%"
        with self.assertRaisesRegex(ValueError, "22,800"):
            _validate_scientific_wording(script)


if __name__ == "__main__":
    unittest.main()
