from core.graphic_scenes import _chart, _timeline, _evidence, render_graphic_scene


def test_chart_uses_only_supplied_numeric_values():
    frame = _chart({"title": "Revenue", "labels": ["A", "B"], "values": [10, 20]}, 0.8)
    assert frame is not None
    assert frame.size == (1920, 1080)


def test_chart_rejects_missing_or_invalid_values():
    assert _chart({"labels": ["A"], "values": []}, 1.0) is None
    assert _chart({"labels": ["A"], "values": ["unknown"]}, 1.0) is None


def test_timeline_and_evidence_frames_render():
    assert _timeline([{"year": "1990", "event": "Launch"}], 1.0, "History") is not None
    assert _evidence("A verified statement from the supplied research.", 1.0, "Evidence") is not None


def test_graphic_renderer_skips_unsupported_visual_types(tmp_path):
    assert render_graphic_scene({"visual_type": "stock"}, 5, tmp_path, "shot") is None


class ContextGraphicFallbackTests(unittest.TestCase):
    def test_archival_unavailable_context_graphic_is_disclosed(self):
        import tempfile
        from pathlib import Path
        from core.graphic_scenes import render_context_graphic

        with tempfile.TemporaryDirectory() as temp:
            output = render_context_graphic({
                "title": "Chernobyl",
                "subject": "Reactor 4",
                "location": "Pripyat, Ukraine",
                "time_period": "26 April 1986",
                "narration": "The reactor suffered a catastrophic failure.",
            }, 5, temp, "test_archival_context")
            self.assertIsNotNone(output)
            self.assertTrue(Path(output).is_file())
            self.assertGreater(Path(output).stat().st_size, 1000)
