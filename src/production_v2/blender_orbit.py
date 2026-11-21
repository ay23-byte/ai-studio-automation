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
parser.add_argument("--scene-kind", choices=("blender_orbit", "blender_solar_system", "blender_atom", "blender_earth_moon", "blender_earth_globe", "blender_volcano", "blender_satellite", "blender_turbine"), default="blender_orbit")
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



def build_earth_globe():
    earth = sphere("Earth globe (schematic)", 1.45, (0, 0, 0), (0.035, 0.22, 0.72), metallic=0.12)
    # Raised, simplified landmasses are illustrative, not a geographic dataset.
    for name, location, scale in (
        ("North America", (-0.55, -0.95, 0.48), (0.48, 0.16, 0.32)),
        ("South America", (-0.25, -0.85, -0.62), (0.20, 0.14, 0.52)),
        ("Africa", (0.45, -0.98, -0.10), (0.30, 0.15, 0.42)),
        ("Eurasia", (0.55, -0.78, 0.62), (0.66, 0.14, 0.30)),
        ("Australia", (1.02, -0.50, -0.58), (0.25, 0.12, 0.16)),
    ):
        land = sphere(name, 0.5, location, (0.12, 0.48, 0.24), emission=0.03)
        land.scale = scale
        land.parent = earth
    for radius, tilt in ((1.48, 0.0), (1.49, 0.75), (1.49, 1.35)):
        orbit_curve("Globe guide", radius, color=(0.12, 0.42, 0.65), tilt=tilt)
    earth.rotation_euler[2] = 0
    earth.keyframe_insert(data_path="rotation_euler", frame=1)
    earth.rotation_euler[2] = 2 * math.pi
    earth.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)


def build_volcano():
    bpy.ops.mesh.primitive_cone_add(vertices=64, radius1=1.85, radius2=0.35, depth=2.6, location=(0, 0, 0.3))
    cone = bpy.context.object
    cone.name = "Volcano cross-section (schematic)"
    cone.data.materials.append(material("Volcanic rock", (0.22, 0.12, 0.09), metallic=0.05))
    sphere("Magma chamber", 0.72, (0, 0, -1.05), (0.95, 0.12, 0.015), emission=0.8)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.22, depth=1.1, location=(0, 0, 1.25))
    vent = bpy.context.object
    vent.name = "Magma vent"
    vent.data.materials.append(material("Molten lava", (1.0, 0.18, 0.015), emission=1.4))
    for index in range(7):
        drop = sphere(f"Lava plume {index + 1}", 0.07 + 0.015 * (index % 3),
                      (0.12 * math.sin(index), 0.10 * math.cos(index), 1.9),
                      (1.0, 0.24, 0.015), emission=1.0)
        for frame, z in ((1, 1.8), (scene.frame_end, 2.4 + 0.18 * index)):
            drop.location.z = z
            drop.keyframe_insert(data_path="location", frame=frame)


def build_satellite():
    sphere("Earth", 1.05, (0, 0, 0), (0.035, 0.22, 0.72), metallic=0.12)
    orbit_curve("Satellite orbital path", 2.55, tilt=0.25)
    root = bpy.data.objects.new("Satellite assembly", None)
    bpy.context.collection.objects.link(root)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    body = bpy.context.object
    body.name = "Satellite bus"
    body.scale = (0.42, 0.32, 0.32)
    body.data.materials.append(material("Satellite gold", (0.82, 0.58, 0.18), metallic=0.7))
    body.parent = root
    for side in (-1, 1):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, side * 0.9, 0))
        panel = bpy.context.object
        panel.name = "Solar panel"
        panel.scale = (0.08, 0.55, 0.42)
        panel.data.materials.append(material("Solar panel blue", (0.025, 0.16, 0.42), metallic=0.6))
        panel.parent = root
    animate_orbit(root, 2.55, tilt=0.25)


def build_turbine():
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.12, depth=2.6, location=(0, 0, 0.65))
    tower = bpy.context.object
    tower.name = "Wind turbine tower"
    tower.data.materials.append(material("Turbine tower", (0.72, 0.78, 0.82), metallic=0.45))
    root = bpy.data.objects.new("Turbine rotor", None)
    bpy.context.collection.objects.link(root)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.24, location=(0, 0, 2.0))
    hub = bpy.context.object
    hub.name = "Rotor hub"
    hub.data.materials.append(material("Rotor hub material", (0.8, 0.84, 0.88), metallic=0.6))
    hub.parent = root
    for index in range(3):
        angle = 2 * math.pi * index / 3
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 2.0))
        blade = bpy.context.object
        blade.name = f"Rotor blade {index + 1}"
        blade.scale = (0.10, 0.12, 1.15)
        blade.location.x = math.sin(angle) * 0.65
        blade.location.z = 2.0 + math.cos(angle) * 0.65
        blade.rotation_euler[1] = angle
        blade.data.materials.append(material("Blade material", (0.82, 0.87, 0.9), metallic=0.35))
        blade.parent = root
    root.location = (0, 0, 2.0)
    root.rotation_euler[1] = 0
    root.keyframe_insert(data_path="rotation_euler", frame=1)
    root.rotation_euler[1] = 2 * math.pi
    root.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)


builders = {
    "blender_orbit": build_simple_orbit,
    "blender_solar_system": build_solar_system,
    "blender_atom": build_atom,
    "blender_earth_moon": build_earth_moon,
    "blender_earth_globe": build_earth_globe,
    "blender_volcano": build_volcano,
    "blender_satellite": build_satellite,
    "blender_turbine": build_turbine,
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
    "blender_earth_globe": "EARTH GLOBE — SIMPLIFIED GEOGRAPHY",
    "blender_volcano": "VOLCANO CROSS-SECTION — SCHEMATIC",
    "blender_satellite": "SATELLITE ORBIT — CONCEPTUAL MODEL",
    "blender_turbine": "WIND TURBINE — CONCEPTUAL MODEL",
}[args.scene_kind]
label.data.size = 0.20
label.data.materials.append(material("Label", (0.8, 0.9, 1.0), emission=0.8))

bpy.ops.render.render(animation=True)
