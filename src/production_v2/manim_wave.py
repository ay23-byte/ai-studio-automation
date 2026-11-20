"""A deliberately limited labelled wave illustration, not a claim-specific simulation."""
from manim import Axes, BLUE, Create, Scene, Text, UP, YELLOW
import numpy as np


class WaveIllustration(Scene):
    def construct(self):
        label = Text("Conceptual sine wave — illustrative", font_size=30).to_edge(UP)
        axes = Axes(x_range=[-3, 3, 1], y_range=[-1.5, 1.5, 1], x_length=10, y_length=4, tips=False)
        wave = axes.plot(lambda x: np.sin(x * 2), x_range=[-3, 3], color=YELLOW)
        axes.set_color(BLUE)
        self.add(label, axes)
        self.play(Create(wave), run_time=3)
        self.wait(1)
