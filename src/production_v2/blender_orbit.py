"""CPU-rendered Blender explainer scenes. All scenes are conceptual, not evidence footage."""
import argparse
import math
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--width", type=int, required=True)
parser.add_argument("--height", type=int, required=True)
parser.add_argument("--fps", type=int, required=True)
parser.add_argument("--scene-kind", choices=("blender_orbit", "blender_solar_system", "blender_atom", "blender_earth_moon"), default="blender_orbit")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
if not (64 <= args.width <= 3840 and 64 <= args.height <= 3840 and 1 <= args.fps <= 60):
    raise ValueError("Unsupported render dimensions or frame rate")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
# Eevee Next is a real-time 3D renderer and keeps CPU-only GitHub builds
# practical; the scene still uses actual Blender geometry, materials and keyframes.
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.eevee.taa_render_samples = 16
scene.render.threads_mode = "FIXED"
scene.render.threads = 2
scene.render.resolution_x = args.width
scene.render.resolution_y = args.height
scene.render.resolution_percentage = 100
scene.render.fps = args.fps
scene.frame_start = 1
scene.frame_end = args.fps * 4
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = args.output
scene.world.color = (0.012, 0.018, 0.035)


def material(name, color, metallic=0.0, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Emission Color"].default_value = (*color, 1)
    bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def sphere(name, radius, location, color, metallic=0.0, emission=0.0):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material(name, color, metallic, emission))
    bpy.ops.object.shade_smooth()
    return obj


def orbit_curve(name, radius, color=(0.18, 0.32, 0.52), tilt=0.0):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.012
    curve.bevel_resolution = 3
    spline = curve.splines.new("POLY")
    spline.points.add(127)
    for i, point in enumerate(spline.points):
        a = 2 * math.pi * i / 128
        point.co = (radius * math.cos(a), radius * math.sin(a) * math.cos(tilt),
                    radius * math.sin(a) * math.sin(tilt), 1)
    spline.use_cyclic_u = True
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material(name + " material", color, emission=0.25))
    return obj


def animate_orbit(obj, radius, phase=0.0, tilt=0.0):
    for frame in range(1, scene.frame_end + 1):
        angle = 2 * math.pi * (frame - 1) / scene.frame_end + phase
        obj.location = (radius * math.cos(angle),
                        radius * math.sin(angle) * math.cos(tilt),
                        radius * math.sin(angle) * math.sin(tilt))
        obj.keyframe_insert(data_path="location", frame=frame)
    if obj.animation_data and obj.animation_data.action:
        for curve in obj.animation_data.action.fcurves:
            for key in curve.keyframe_points:
                key.interpolation = "LINEAR"


def build_simple_orbit():
    sphere("Central body", 0.55, (0, 0, 0), (1.0, 0.48, 0.08), emission=0.25)
    orbit_curve("Orbital path", 2.0)
    body = sphere("Orbiting body", 0.22, (2, 0, 0), (0.08, 0.5, 1.0), metallic=0.25)
    animate_orbit(body, 2.0)


def build_solar_system():
    sphere("Sun", 0.58, (0, 0, 0), (1.0, 0.38, 0.035), emission=1.2)
    planets = [
        (1.15, 0.10, (0.65, 0.62, 0.55), 1.0),
        (1.75, 0.16, (0.2, 0.55, 0.95), 0.72),
        (2.45, 0.13, (0.85, 0.22, 0.10), 0.52),
        (3.25, 0.25, (0.85, 0.58, 0.32), 0.36),
    ]
    for index, (radius, size, color, speed) in enumerate(planets, 1):
        orbit_curve(f"Planet {index} orbit", radius, tilt=0.05 * index)
        planet = sphere(f"Planet {index}", size, (radius, 0, 0), color, metallic=0.12)
        animate_orbit(planet, radius, phase=index * 0.7, tilt=0.05 * index)
    # Explicitly label the model as schematic; sizes and orbital distances are not to scale.


def build_atom():
    sphere("Nucleus", 0.42, (0, 0, 0), (0.95, 0.30, 0.16), emission=0.25)
    for index, angle in enumerate((0.0, 2.1, 4.2)):
        tilt = (index - 1) * 0.62
        orbit_curve(f"Electron shell {index + 1}", 1.65, tilt=tilt)
        electron = sphere(f"Electron {index + 1}", 0.12, (1.65, 0, 0), (0.15, 0.65, 1.0), emission=0.3)
        animate_orbit(electron, 1.65, phase=angle, tilt=tilt)


def build_earth_moon():
    earth = sphere("Earth (schematic)", 0.48, (0, 0, 0), (0.06, 0.28, 0.82), metallic=0.08)
    earth.rotation_euler[2] = 0
    earth.keyframe_insert(data_path="rotation_euler", frame=1)
    earth.rotation_euler[2] = 2 * math.pi
    earth.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
    orbit_curve("Moon orbital path", 2.0, tilt=0.18)
    moon = sphere("Moon", 0.16, (2, 0, 0), (0.62, 0.65, 0.72))
    animate_orbit(moon, 2.0, tilt=0.18)


builders = {
    "blender_orbit": build_simple_orbit,
    "blender_solar_system": build_solar_system,
    "blender_atom": build_atom,
    "blender_earth_moon": build_earth_moon,
}
builders[args.scene_kind]()

bpy.ops.object.camera_add(location=(0, -0.6, 11))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = 8.2 if args.width >= args.height else 11.5
scene.camera = camera
bpy.ops.object.light_add(type="AREA", location=(0, -1, 7))
bpy.context.object.data.energy = 1500
bpy.context.object.data.shape = "DISK"
bpy.context.object.data.size = 9

bpy.ops.object.text_add(location=(-3.4, 3.3, 0.2))
label = bpy.context.object
label.data.body = {
    "blender_orbit": "CONCEPTUAL ORBIT — NOT TO SCALE",
    "blender_solar_system": "SOLAR SYSTEM MODEL — NOT TO SCALE",
    "blender_atom": "ATOMIC MODEL — SCHEMATIC",
    "blender_earth_moon": "EARTH–MOON MODEL — SCHEMATIC",
}[args.scene_kind]
label.data.size = 0.20
label.data.materials.append(material("Label", (0.8, 0.9, 1.0), emission=0.8))

bpy.ops.render.render(animation=True)
