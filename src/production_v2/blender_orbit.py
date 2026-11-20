"""Blender background script: conceptual circular orbit, not to scale.

Run with Blender, not ordinary Python. This file intentionally accepts only
output dimensions, FPS and a destination controlled by the caller.
"""
import argparse
import math
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--width', type=int, required=True)
parser.add_argument('--height', type=int, required=True)
parser.add_argument('--fps', type=int, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
if not (64 <= args.width <= 3840 and 64 <= args.height <= 3840 and 1 <= args.fps <= 60):
    raise ValueError('Unsupported render dimensions or frame rate')

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x = args.width
scene.render.resolution_y = args.height
scene.render.resolution_percentage = 100
scene.render.fps = args.fps
scene.frame_start = 1
scene.frame_end = args.fps * 4
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = args.output
scene.world.color = (0.025, 0.025, 0.025)


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    return mat


def sphere(name, radius, location, color):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material(name, color))
    bpy.ops.object.shade_smooth()
    return obj

sphere('central body', 0.5, (0, 0, 0), (1, 0.55, 0.08))
orbiter = sphere('orbiting body', 0.22, (2, 0, 0), (0.08, 0.5, 1))
# An idealized circular orbit, linear interpolation between sampled positions.
for frame in range(1, scene.frame_end + 1):
    angle = 2 * math.pi * (frame - 1) / scene.frame_end
    orbiter.location = (2 * math.cos(angle), 2 * math.sin(angle), 0)
    orbiter.keyframe_insert(data_path='location', frame=frame)
if orbiter.animation_data and orbiter.animation_data.action:
    for curve in orbiter.animation_data.action.fcurves:
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'

bpy.ops.object.camera_add(location=(0, 0, 10))
camera = bpy.context.object
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 6.5 if args.width >= args.height else 10
scene.camera = camera
bpy.ops.object.light_add(type='AREA', location=(0, 0, 5))
bpy.context.object.data.energy = 900
bpy.context.object.data.shape = 'DISK'
bpy.context.object.data.size = 8
bpy.ops.object.text_add(location=(-2.8, 2.6, 0))
label = bpy.context.object
label.data.body = 'Conceptual circular orbit - not to scale'
label.data.size = 0.18
label.data.materials.append(material('label', (1, 1, 1)))

bpy.ops.render.render(animation=True)
