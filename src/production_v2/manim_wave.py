"""A deliberately limited labelled wave illustration, not a claim-specific simulation."""
from manim import Axes, BLUE, Create, DOWN, Scene, Text, UP, YELLOW, config
import numpy as np


class WaveIllustration(Scene):
    def construct(self):
        # Size the diagram to the requested render aspect ratio. This keeps the
        # same renderer legible in both 9:16 Shorts and 16:9 documentaries.
        frame_width = float(config.frame_width)
        frame_height = float(config.frame_height)
        label = Text("Conceptual sine wave — illustrative",
                     font_size=min(30, max(18, frame_width * 5))).to_edge(UP)
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-1.5, 1.5, 1],
            x_length=min(10, frame_width * 0.82),
            y_length=min(4, frame_height * 0.38),
            tips=False,
        ).move_to(DOWN * 0.25)
        wave = axes.plot(lambda x: np.sin(x * 2), x_range=[-3, 3], color=YELLOW)
        axes.set_color(BLUE)
        self.add(label, axes)
        self.play(Create(wave), run_time=3)
        self.wait(1)
