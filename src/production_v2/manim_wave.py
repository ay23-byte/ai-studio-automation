"""Short, labelled Manim explainers used only for matching educational narration."""
import numpy as np
from manim import (
    Arrow, Axes, BLUE, Circle, Create, Dot, DOWN, FadeIn, GREEN, LEFT, Line,
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



class CalculusExplainer(Scene):
    def construct(self):
        axes = Axes(x_range=[-2, 2, 1], y_range=[-1, 4, 1],
                    x_length=min(9, float(config.frame_width) * 0.78),
                    y_length=min(4.5, float(config.frame_height) * 0.40),
                    tips=False).move_to(DOWN * 0.25)
        curve = axes.plot(lambda x: x ** 2, x_range=[-1.8, 1.8], color=YELLOW)
        tangent = axes.plot(lambda x: 2 * x - 1, x_range=[0, 2], color=GREEN)
        point = Dot(axes.c2p(1, 1), color=RED)
        self.add(title("CALCULUS — TANGENT AND DERIVATIVE"), axes)
        self.play(Create(curve), run_time=1.8)
        self.play(Create(tangent), FadeIn(point), run_time=1.6)
        self.wait(0.6)


class FieldLinesExplainer(Scene):
    def construct(self):
        positive = Circle(radius=0.32, color=RED).move_to(LEFT * 2)
        negative = Circle(radius=0.32, color=BLUE).move_to(RIGHT * 2)
        plus = Text("+", font_size=30).move_to(positive)
        minus = Text("−", font_size=30).move_to(negative)
        arrows = VGroup(*[
            Arrow(LEFT * 1.62 + UP * y, RIGHT * 1.62 + UP * y * 0.72,
                  buff=0.08, color=YELLOW, stroke_width=4)
            for y in (-1.2, -0.8, -0.4, 0.0, 0.4, 0.8, 1.2)
        ])
        self.add(title("ELECTRIC FIELD — SCHEMATIC LINES"))
        self.play(FadeIn(positive), FadeIn(negative), FadeIn(plus), FadeIn(minus), run_time=1)
        self.play(Create(arrows), run_time=2)
        self.wait(1)


class FourierExplainer(Scene):
    def construct(self):
        axes = Axes(x_range=[-np.pi, np.pi, np.pi / 2], y_range=[-1.6, 1.6, 1],
                    x_length=min(9, float(config.frame_width) * 0.78),
                    y_length=min(4.5, float(config.frame_height) * 0.40),
                    tips=False).move_to(DOWN * 0.25)
        first = axes.plot(lambda x: np.sin(x), x_range=[-np.pi, np.pi], color=BLUE)
        third = axes.plot(lambda x: 0.5 * np.sin(3 * x), x_range=[-np.pi, np.pi], color=GREEN)
        combined = axes.plot(lambda x: np.sin(x) + 0.5 * np.sin(3 * x),
                             x_range=[-np.pi, np.pi], color=YELLOW)
        self.add(title("FOURIER SERIES — HARMONIC SUM"), axes)
        self.play(Create(first), Create(third), run_time=1.6)
        self.play(Create(combined), run_time=1.8)
        self.wait(0.6)


class NeuralNetworkExplainer(Scene):
    def construct(self):
        layer_x = [-2.8, 0.0, 2.8]
        layer_sizes = [3, 4, 2]
        layers = []
        for x, count in zip(layer_x, layer_sizes):
            ys = np.linspace(-1.45, 1.45, count)
            layers.append([np.array([x, float(y), 0.0]) for y in ys])
        edges = []
        for left, right in zip(layers[:-1], layers[1:]):
            for start in left:
                for end in right:
                    edges.append(Line(start, end, color=BLUE, stroke_width=2))
        nodes = []
        for layer_index, layer in enumerate(layers):
            for point in layer:
                nodes.append(Circle(radius=0.16, color=GREEN if layer_index == 1 else YELLOW)
                             .move_to(point))
        self.add(title("NEURAL NETWORK — LAYERED MODEL"))
        self.play(Create(VGroup(*edges)), run_time=1.8)
        self.play(FadeIn(VGroup(*nodes)), run_time=1.2)
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
