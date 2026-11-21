"""Short, labelled Manim explainers used only for matching educational narration."""
import numpy as np
from manim import (
    Arrow, Axes, BLUE, Create, Dot, DOWN, FadeIn, GREEN, LEFT, Line,
    MathTex, ORIGIN, RED, RIGHT, Scene, Text, UP, VGroup, YELLOW, config,
)


def title(text):
    return Text(text, font_size=min(30, max(18, float(config.frame_width) * 5))).to_edge(UP)


class WaveIllustration(Scene):
    def construct(self):
        axes = Axes(x_range=[-3, 3, 1], y_range=[-1.5, 1.5, 1],
                    x_length=min(10, float(config.frame_width) * 0.82),
                    y_length=min(4, float(config.frame_height) * 0.38), tips=False).move_to(DOWN * 0.25)
        wave = axes.plot(lambda x: np.sin(x * 2), x_range=[-3, 3], color=YELLOW)
        axes.set_color(BLUE)
        self.add(title("SINE WAVE — CONCEPTUAL"), axes)
        self.play(Create(wave), run_time=3)
        self.wait(1)


class ProjectileMotion(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 6, 1], y_range=[0, 4, 1],
                    x_length=min(9, float(config.frame_width) * 0.78),
                    y_length=min(4.5, float(config.frame_height) * 0.40),
                    tips=False).move_to(DOWN * 0.25)
        path = axes.plot(lambda t: 0.42 * t * (6 - t), x_range=[0, 6], color=YELLOW)
        marker = Dot(axes.c2p(0, 0), color=RED)
        self.add(title("PROJECTILE MOTION — IDEALIZED"), axes)
        self.play(Create(path), run_time=1.8)
        self.play(marker.animate.move_to(axes.c2p(6, 0)), run_time=1.8, rate_func=lambda t: t)
        self.wait(0.4)


class VectorAddition(Scene):
    def construct(self):
        axes = Axes(x_range=[-1, 5, 1], y_range=[-1, 4, 1],
                    x_length=min(9, float(config.frame_width) * 0.78),
                    y_length=min(4.5, float(config.frame_height) * 0.40),
                    tips=False).move_to(DOWN * 0.25)
        start = axes.c2p(0, 0)
        first_end = axes.c2p(3, 0)
        result_end = axes.c2p(3, 2)
        first = Arrow(start, first_end, buff=0, color=BLUE, stroke_width=8)
        second = Arrow(first_end, result_end, buff=0, color=GREEN, stroke_width=8)
        resultant = Arrow(start, result_end, buff=0, color=YELLOW, stroke_width=8)
        self.add(title("VECTOR ADDITION — HEAD TO TAIL"), axes)
        self.play(Create(first), run_time=0.9)
        self.play(Create(second), run_time=0.9)
        self.play(FadeIn(resultant), run_time=1.2)
        self.wait(1)
