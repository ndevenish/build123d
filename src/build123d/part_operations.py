"""
Part Operations

name: part_operations.py
by:   Gumyr
date: March 17th 2023

desc:
    This python module contains operations (functions) that work on Parts.

license:

    Copyright 2023 Gumyr

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

"""
from __future__ import annotations
from math import radians, tan
from typing import Union, Iterable
from build123d.build_enums import Mode, Until, Transition, Align
from build123d.build_part import BasePartObject, BuildPart
from build123d.geometry import (
    Axis,
    Location,
    Plane,
    Rotation,
    RotationLike,
    Vector,
    VectorLike,
)
from build123d.topology import (
    Compound,
    Edge,
    Face,
    Shell,
    Solid,
    Wire,
    Part,
    Sketch,
)

from build123d.build_common import (
    Builder,
    logger,
    LocationList,
    WorkplaneList,
    validate_inputs,
)

#
# Operations
#


# def extrude(
#     to_extrude: Face = None,
#     amount: float = None,
#     until: Until = None,
#     both: bool = False,
#     taper: float = 0.0,
#     clean: bool = True,
#     mode: Mode = Mode.ADD,
# ):
#     """Part Operation: Extrude

#     Extrude a sketch/face and combine with part.

#     Args:
#         to_extrude (Face): working face, if not provided use pending_faces.
#             Defaults to None.
#         amount (float): distance to extrude, sign controls direction
#             Defaults to None.
#         until (Until): extrude limit
#         both (bool, optional): extrude in both directions. Defaults to False.
#         taper (float, optional): taper angle. Defaults to 0.
#         clean (bool, optional): Remove extraneous internal structure. Defaults to True.
#         mode (Mode, optional): combination mode. Defaults to Mode.ADD.
#     """
#     if is_algcompound(to_extrude):
#         context = None
#     else:
#         # context: BuildPart = BuildPart._get_context()
#         context: BuildPart = BuildPart._current.get(None)
#         # validate_inputs(context, self, sections)
#         # context.validate_inputs(self, [to_extrude])

#     new_solids: list[Solid] = []
#     if not to_extrude and not context.pending_faces:
#         context: BuildPart = BuildPart._get_context(self)

#     if to_extrude:
#         list_context = LocationList._get_context()
#         workplane_context = WorkplaneList._get_context()
#         faces, face_planes = [], []
#         for plane in workplane_context.workplanes:
#             for location in list_context.local_locations:
#                 faces.append(to_extrude.moved(location))
#                 face_planes.append(plane)
#     else:
#         faces = context.pending_faces
#         face_planes = context.pending_face_planes
#         context.pending_faces = []
#         context.pending_face_planes = []

#     logger.info(
#         "%d face(s) to extrude on %d face plane(s)",
#         len(faces),
#         len(face_planes),
#     )

#     for face, plane in zip(faces, face_planes):
#         for direction in [1, -1] if both else [1]:
#             if amount:
#                 new_solids.append(
#                     Solid.extrude_linear(
#                         section=face,
#                         normal=plane.z_dir * amount * direction,
#                         taper=taper,
#                     )
#                 )
#             else:
#                 new_solids.append(
#                     Solid.extrude_until(
#                         section=face,
#                         target_object=context.part,
#                         direction=plane.z_dir * direction,
#                         until=until,
#                     )
#                 )

#     context._add_to_context(*new_solids, clean=clean, mode=mode)
#     super().__init__(Compound.make_compound(new_solids).wrapped)


def loft(
    *sections: Face,
    ruled: bool = False,
    clean: bool = True,
    mode: Mode = Mode.ADD,
):
    """Part Operation: loft

    Loft the pending sketches/faces, across all workplanes, into a solid.

    Args:
        sections (Face): sequence of loft sections. If not provided, pending_faces
            will be used.
        ruled (bool, optional): discontiguous layer tangents. Defaults to False.
        clean (bool, optional): Remove extraneous internal structure. Defaults to True.
        mode (Mode, optional): combination mode. Defaults to Mode.ADD.
    """
    context: BuildPart = BuildPart._current.get(None)

    if not sections:
        loft_wires = [face.outer_wire() for face in context.pending_faces]
        context.pending_faces = []
        context.pending_face_planes = []
    else:
        loft_wires = [
            face.outer_wire() for section in sections for face in section.faces()
        ]
    new_solid = Solid.make_loft(loft_wires, ruled)

    # Try to recover an invalid loft
    if not new_solid.is_valid():
        new_solid = Solid.make_solid(
            Shell.make_shell(new_solid.faces() + list(sections))
        )
        if clean:
            new_solid = new_solid.clean()
        if not new_solid.is_valid():
            raise RuntimeError("Failed to create valid loft")

    if context is not None:
        context._add_to_context(new_solid, clean=clean, mode=mode)

    return Part(Compound.make_compound([new_solid]).wrapped)


def revolve(
    *profiles: Face,
    axis: Axis,
    revolution_arc: float = 360.0,
    clean: bool = True,
    mode: Mode = Mode.ADD,
):
    """Part Operation: Revolve

    Revolve the profile or pending sketches/face about the given axis.

    Args:
        profiles (Face, optional): sequence of 2D profile to revolve.
        axis (Axis): axis of rotation.
        revolution_arc (float, optional): angular size of revolution. Defaults to 360.0.
        clean (bool, optional): Remove extraneous internal structure. Defaults to True.
        mode (Mode, optional): combination mode. Defaults to Mode.ADD.

    Raises:
        ValueError: Invalid axis of revolution
    """
    context: BuildPart = BuildPart._current.get(None)

    # Make sure we account for users specifying angles larger than 360 degrees, and
    # for OCCT not assuming that a 0 degree revolve means a 360 degree revolve
    angle = revolution_arc % 360.0
    angle = 360.0 if angle == 0 else angle

    if not profiles:
        profiles = context.pending_faces
        context.pending_faces = []
        context.pending_face_planes = []
    else:
        p = []
        for profile in profiles:
            p.extend(profile.faces())
        profiles = p

    new_solids = []
    for profile in profiles:
        # axis origin must be on the same plane as profile
        face_plane = Plane(profile)
        if not face_plane.contains(axis.position):
            raise ValueError(
                "axis origin must be on the same plane as the face to revolve"
            )
        if not face_plane.contains(axis):
            raise ValueError("axis must be in the same plane as the face to revolve")

        new_solid = Solid.revolve(profile, angle, axis)
        locations = LocationList._get_context().locations if context else [Location()]
        new_solids.extend([new_solid.moved(location) for location in locations])

    if context is not None:
        context._add_to_context(*new_solids, clean=clean, mode=mode)

    return Part(Compound.make_compound(new_solids).wrapped)


def section(
    section_by: Union[Plane, Iterable[Plane]] = None,
    obj: Part = None,
    height: float = 0.0,
    clean: bool = True,
    mode: Mode = Mode.INTERSECT,
):
    """Part Operation: section

    Slices current part at the given height by section_by or current workplane(s).

    Args:
        section_by (Plane, optional): sequence of planes to section object.
            Defaults to None.
        obj (Part, optional): object to section. Defaults to None.
        height (float, optional): workplane offset. Defaults to 0.0.
        clean (bool, optional): Remove extraneous internal structure. Defaults to True.
        mode (Mode, optional): combination mode. Defaults to Mode.INTERSECT.
    """
    context: BuildPart = BuildPart._current.get(None)

    if context is not None and obj is None:
        max_size = context.part.bounding_box().diagonal
    else:
        max_size = obj.bounding_box().diagonal

    if context:
        section_planes = WorkplaneList._get_context().workplanes
    else:
        section_planes = (
            section_by if isinstance(section_by, Iterable) else [section_by]
        )

    planes = [
        Face.make_rect(
            2 * max_size,
            2 * max_size,
            Plane(origin=plane.origin + plane.z_dir * height, z_dir=plane.z_dir),
        )
        for plane in section_planes
    ]

    if context is not None:
        context._add_to_context(*planes, faces_to_pending=False, clean=clean, mode=mode)
        result = planes
    else:
        result = [obj.intersect(plane) for plane in planes]

    return Part(Compound.make_compound(result).wrapped)


def sweep(
    *sections: Union[Face, Compound, Sketch],
    path: Union[Edge, Wire] = None,
    multisection: bool = False,
    is_frenet: bool = False,
    transition: Transition = Transition.TRANSFORMED,
    normal: VectorLike = None,
    binormal: Union[Edge, Wire] = None,
    clean: bool = True,
    mode: Mode = Mode.ADD,
) -> Part:
    """Part Operation: sweep

    Sweep pending sketches/faces along path.

    Args:
        sections (Union[Face, Compound]): sequence of sections to sweep
        path (Union[Edge, Wire], optional): path to follow.
            Defaults to context pending_edges.
        multisection (bool, optional): sweep multiple on path. Defaults to False.
        is_frenet (bool, optional): use frenet algorithm. Defaults to False.
        transition (Transition, optional): discontinuity handling option.
            Defaults to Transition.RIGHT.
        normal (VectorLike, optional): fixed normal. Defaults to None.
        binormal (Union[Edge, Wire], optional): guide rotation along path. Defaults to None.
        clean (bool, optional): Remove extraneous internal structure. Defaults to True.
        mode (Mode, optional): combination. Defaults to Mode.ADD.
    """
    context: BuildPart = BuildPart._current.get(None)

    if path is None:
        path_wire = context.pending_edges_as_wire
        context.pending_edges = []
    else:
        path_wire = Wire.make_wire([path]) if isinstance(path, Edge) else path

    if sections:
        section_list = [face for section in sections for face in section.faces()]
    else:
        section_list = context.pending_faces
        context.pending_faces = []
        context.pending_face_planes = []

    if binormal is None and normal is not None:
        binormal_mode = Vector(normal)
    elif isinstance(binormal, Edge):
        binormal_mode = Wire.make_wire([binormal])
    else:
        binormal_mode = binormal

    new_solids = []
    locations = LocationList._get_context().locations if context else [Location()]
    for location in locations:
        if multisection:
            sections = [section.outer_wire() for section in section_list]
            new_solid = Solid.sweep_multi(
                sections, path_wire, True, is_frenet, binormal_mode
            ).moved(location)
        else:
            for section in section_list:
                new_solid = Solid.sweep(
                    section=section,
                    path=path_wire,
                    make_solid=True,
                    is_frenet=is_frenet,
                    mode=binormal_mode,
                    transition=transition,
                ).moved(location)
        new_solids.append(new_solid)

    if context is not None:
        context._add_to_context(*new_solids, clean=clean, mode=mode)

    return Part(Compound.make_compound(new_solids).wrapped)
