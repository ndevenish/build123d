"""
build123d exporter/import for 3MF and STL

name: mesher.py
by:   Gumyr
date: Aug 9th 2023

desc:
    This module provides the Mesher class that implements exporting and importing 
    both 3MF and STL mesh files.  It uses the 3MF Consortium's Lib3MF library
    (see https://github.com/3MFConsortium/lib3mf).

    Creating a 3MF object involves constructing a valid 3D model conforming to
    the 3MF specification. The resource hierarchy represents the various
    components that make up a 3MF object. The main components required to create
    a 3MF object are:

        Wrapper: The wrapper is the highest-level component representing the
    entire 3MF model. It serves as a container for all other resources and
    provides access to the complete 3D model. The wrapper is the starting point
    for creating and managing the 3MF model.

        Model: The model is a core component that contains the geometric and
    non-geometric resources of the 3D object. It represents the actual 3D
    content, including geometry, materials, colors, textures, and other model
    information.

        Resources: Within the model, various resources are used to define
    different aspects of the 3D object. Some essential resources are:

        a. Mesh: The mesh resource defines the geometry of the 3D object. It
    contains a collection of vertices, triangles, and other geometric
    information that describes the shape.

        b. Components: Components allow you to define complex structures by
    combining multiple meshes together. They are useful for hierarchical
    assemblies and instances.

        c. Materials: Materials define the appearance properties of the
    surfaces, such as color, texture, or surface finish.

        d. Textures: Textures are images applied to the surfaces of the 3D
    object to add detail and realism.

        e. Colors: Colors represent color information used in the 3D model,
    which can be applied to vertices or faces.

        Build Items: Build items are the instances of resources used in the 3D
    model. They specify the usage of resources within the model. For example, a
    build item can refer to a specific mesh, material, and transformation to
    represent an instance of an object.

        Metadata: Metadata provides additional information about the model, such
    as author, creation date, and custom properties.

        Attachments: Attachments can include additional files or data associated
    with the 3MF object, such as texture images or other external resources.

    When creating a 3MF object, you typically start with the wrapper and then
    create or import the necessary resources, such as meshes, materials, and
    textures, to define the 3D content. You then organize the model using build
    items, specifying how the resources are used in the scene. Additionally, you
    can add metadata and attachments as needed to complete the 3MF object.

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


import copy
import ctypes
import os
import sys
import uuid
import warnings
from typing import Iterable, Union

from build123d.build_enums import MeshType, Unit
from build123d.geometry import Color, Vector
from build123d.topology import downcast, Compound, Shape, Shell, Solid
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
)
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopLoc import TopLoc_Location
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.BRepGProp import BRepGProp_Face
from py_lib3mf import Lib3MF
from ocp_vscode import *


class Mesher:
    """Mesher

    Tool for exporting and import meshed objects stored in 3MF or STL files.

    Args:
        unit (Unit, optional): model units. Defaults to Unit.MM.
    """

    # Translate b3d Units to Lib3MF ModelUnits
    map_b3d_to_3mf_unit = {
        Unit.MC: Lib3MF.ModelUnit.MicroMeter,
        Unit.MM: Lib3MF.ModelUnit.MilliMeter,
        Unit.CM: Lib3MF.ModelUnit.CentiMeter,
        Unit.IN: Lib3MF.ModelUnit.Inch,
        Unit.FT: Lib3MF.ModelUnit.Foot,
        Unit.M: Lib3MF.ModelUnit.Meter,
    }
    # Translate Lib3MF ModelUnits to b3d Units
    map_3mf_to_b3d_unit = {v: k for k, v in map_b3d_to_3mf_unit.items()}

    # Translate b3d MeshTypes to 3MF ObjectType
    map_b3d_mesh_type_3mf = {
        MeshType.OTHER: Lib3MF.ObjectType.Other,
        MeshType.MODEL: Lib3MF.ObjectType.Model,
        MeshType.SUPPORT: Lib3MF.ObjectType.Support,
        MeshType.SOLIDSUPPORT: Lib3MF.ObjectType.SolidSupport,
    }
    # Translate 3MF ObjectType to b3d MeshTypess
    map_3mf_to_b3d_mesh_type = {v: k for k, v in map_b3d_mesh_type_3mf.items()}

    def __init__(self, unit: Unit = Unit.MM):
        self.unit = unit
        libpath = os.path.dirname(Lib3MF.__file__)
        self.wrapper = Lib3MF.Wrapper(os.path.join(libpath, "lib3mf"))
        self.model = self.wrapper.CreateModel()
        self.model.SetUnit(Mesher.map_b3d_to_3mf_unit[unit])
        self.meshes: list[Lib3MF.MeshObject] = []

    @property
    def model_unit(self) -> Unit:
        """Unit used in the model"""
        return self.unit

    @property
    def triangle_counts(self) -> list[int]:
        """Number of triangles in each of the model's meshes"""
        return [m.GetTriangleCount() for m in self.meshes]

    @property
    def vertex_counts(self) -> list[int]:
        """Number of vertices in each of the models's meshes"""
        return [m.GetVertexCount() for m in self.meshes]

    @property
    def mesh_count(self) -> int:
        """Number of meshes in the model"""
        mesh_iterator: Lib3MF.MeshObjectIterator = self.model.GetMeshObjects()
        return mesh_iterator.Count()

    @property
    def library_version(self) -> str:
        """3MF Consortium Lib#MF version"""
        major, minor, micro = self.wrapper.GetLibraryVersion()
        return f"{major}.{minor}.{micro}"

    def add_meta_data(
        self,
        name_space: str,
        name: str,
        value: str,
        metadata_type: str,
        must_preserve: bool,
    ):
        """add_meta_data

        Add meta data to the models

        Args:
            name_space (str): categorizer of different metadata entries
            name (str): metadata label
            value (str): metadata content
            metadata_type (str): metadata trype
            must_preserve (bool): metadata must not be removed if unused
        """
        # Get an existing meta data group if there is one
        mdg = self.model.GetMetaDataGroup()
        if mdg is None:
            # Create a components object to attach the meta data group
            components: Lib3MF.ComponentsObject = self.model.AddComponentsObject()
            mdg = components.GetMetaDataGroup()

        # Add the meta data
        mdg.AddMetaData(name_space, name, value, metadata_type, must_preserve)

    def add_code_to_metadata(self):
        """Add the code calling this method to the 3MF metadata with the custom
        name space `build123d`, name equal to the base file name and the type
        as `python`"""
        caller_file = sys._getframe().f_back.f_code.co_filename
        code_file = open(caller_file, "r")  # open code file in read mode
        source_code = code_file.read()  # read whole file to a string
        code_file.close()

        self.add_meta_data(
            name_space="build123d",
            name=os.path.basename(caller_file),
            value=source_code,
            metadata_type="python",
            must_preserve=False,
        )

    def get_meta_data(self) -> list[str]:
        """Retrieve all of the metadata"""
        meta_data_group = self.model.GetMetaDataGroup()
        meta_data_contents = []
        for i in range(meta_data_group.GetMetaDataCount()):
            meta_data = meta_data_group.GetMetaData(i)
            meta_data_contents.append(f"Name Space: {meta_data.GetNameSpace()}")
            meta_data_contents.append(f"Name: {meta_data.GetName()}")
            meta_data_contents.append(f"Type: {meta_data.GetType()}")
            meta_data_contents.append(f"Value: {meta_data.GetValue()}")
        return meta_data_contents

    def get_meta_data_by_key(self, name_space: str, name: str) -> list[str]:
        """Retrive the metadata value and type for the provided name space and name"""
        meta_data_group = self.model.GetMetaDataGroup()
        meta_data_contents = []
        meta_data = meta_data_group.GetMetaDataByKey(name_space, name)
        meta_data_contents.append(f"Type: {meta_data.GetType()}")
        meta_data_contents.append(f"Value: {meta_data.GetValue()}")
        return meta_data_contents

    def get_mesh_properties(self) -> list[str]:
        """Retrieve the properties from all the meshes"""
        properties = []
        for mesh in self.meshes:
            properties += f"Name: {mesh.GetName()}"
            properties += f"Part Number: {mesh.GetPartNumber()}"
            properties += f"Type: {Mesher.map_3mf_to_b3d_mesh_type[Lib3MF.ObjectType(mesh.GetType())].name}"
            uuid_valid, uuid_value = mesh.GetUUID()
            if uuid_valid:
                properties += f"UUID: {uuid_value}"

    def add_shape(
        self,
        shape: Union[Shape, Iterable[Shape]],
        linear_deflection: float = 0.5,
        angular_deflection: float = 0.5,
        mesh_type: MeshType = MeshType.MODEL,
        part_number: str = None,
        uuid: uuid = None,
    ):
        """add_shape

        Add a shape to the 3MF/STL file.

        Args:
            shape (Union[Shape, Iterable[Shape]]): build123d object
            linear_deflection (float, optional): mesh control for edges. Defaults to 0.5.
            angular_deflection (float, optional): mesh control for non-planar surfaces. Defaults to 0.5.
            mesh_type (MeshType, optional): 3D printing use of mesh. Defaults to MeshType.MODEL.
            part_number (str, optional): part #. Defaults to None.
            uuid (uuid, optional): uuid from uuid package. Defaults to None.

        Rasises:
            RuntimeError: 3mf mesh is invalid
            Warning: Degenerate shape skipped
            Warning: 3mf mesh is not manifold
        """

        def _is_facet_forward(
            points: tuple[gp_Pnt, gp_Pnt, gp_Pnt], shape_center: Vector
        ) -> bool:
            # Create the facet
            polygon_builder = BRepBuilderAPI_MakePolygon(*points, Close=True)
            face_builder = BRepBuilderAPI_MakeFace(polygon_builder.Wire())
            facet = face_builder.Face()
            # Find its center & normal
            surface = BRep_Tool.Surface_s(facet)
            projector = GeomAPI_ProjectPointOnSurf(points[0], surface)
            u_val, v_val = projector.LowerDistanceParameters()
            center_gp_pnt = gp_Pnt()
            normal_gp_vec = gp_Vec()
            BRepGProp_Face(facet).Normal(u_val, v_val, center_gp_pnt, normal_gp_vec)
            facet_normal = Vector(normal_gp_vec)
            # Does the facet normal point to the center
            return facet_normal.get_angle(shape_center - Vector(center_gp_pnt)) > 90

        input_shapes = shape if isinstance(shape, Iterable) else [shape]
        shapes = []
        for shape in input_shapes:
            if isinstance(shape, Compound):
                shapes.extend(list(shape))
            else:
                shapes.append(shape)

        loc = TopLoc_Location()  # Face locations

        for shape in shapes:
            shape_center = shape.center()
            # Mesh the shape
            ocp_mesh = copy.deepcopy(shape)
            BRepMesh_IncrementalMesh(
                theShape=ocp_mesh.wrapped,
                theLinDeflection=linear_deflection,
                isRelative=True,
                theAngDeflection=angular_deflection,
                isInParallel=False,
            )

            ocp_mesh_vertices = []
            triangles = []
            offset = 0
            for ocp_face in ocp_mesh.faces():
                # Triangulate the face
                poly_triangulation = BRep_Tool.Triangulation_s(ocp_face.wrapped, loc)
                trsf = loc.Transformation()
                # Store the vertices in the triangulated face
                node_count = poly_triangulation.NbNodes()
                for i in range(1, node_count + 1):
                    gp_pnt = poly_triangulation.Node(i).Transformed(trsf)
                    pnt = (gp_pnt.X(), gp_pnt.Y(), gp_pnt.Z())
                    ocp_mesh_vertices.append(pnt)

                # Store the triangles from the triangulated faces
                for tri in poly_triangulation.Triangles():
                    triangles.append([tri.Value(i) + offset - 1 for i in [1, 2, 3]])
                offset += node_count

            if len(ocp_mesh_vertices) < 3 or not triangles:
                warnings.warn(f"Degenerate shape {shape} - skipped")
                continue

            # Create a lookup table of face vertex to shape vertex
            unique_vertices = list(set(ocp_mesh_vertices))
            vert_table = {
                i: unique_vertices.index(pnt) for i, pnt in enumerate(ocp_mesh_vertices)
            }

            # Create a 3MF mesh object
            mesh_3mf: Lib3MF.MeshObject = self.model.AddMeshObject()

            # Add the meta data
            mesh_3mf.SetType(Mesher.map_b3d_mesh_type_3mf[mesh_type])
            if shape.label:
                mesh_3mf.SetName(shape.label)
            if part_number:
                mesh_3mf.SetPartNumber(part_number)
            if uuid:
                mesh_3mf.SetUUID(str(uuid))
            # mesh_3mf.SetAttachmentAsThumbnail
            # mesh_3mf.SetPackagePart

            # Create vertex list of 3MF positions
            vertices_3mf = []
            gp_pnts = []
            for pnt in unique_vertices:
                c_float_Array_3 = ctypes.c_float * 3
                c_array = c_float_Array_3(*pnt)
                vertices_3mf.append(Lib3MF.Position(c_array))
                gp_pnts.append(gp_Pnt(*pnt))
                # mesh_3mf.AddVertex  Should AddVertex be used to save memory?

            # Create triangle point list
            triangles_3mf = []
            for vertex_indices in triangles:
                triangle_points = (
                    gp_pnts[vert_table[vertex_indices[0]]],
                    gp_pnts[vert_table[vertex_indices[1]]],
                    gp_pnts[vert_table[vertex_indices[2]]],
                )
                order = (
                    [0, 2, 1]
                    if not _is_facet_forward(triangle_points, shape_center)
                    else [0, 1, 2]
                )
                # order = [2, 1, 0] # Creates an invalid mesh
                ordered_indices = [vertex_indices[i] for i in order]
                c_uint_Array_3 = ctypes.c_uint * 3
                mapped_indices = [vert_table[i] for i in ordered_indices]
                # Remove degenerate triangles
                if len(set(mapped_indices)) != 3:
                    continue
                c_array = c_uint_Array_3(*mapped_indices)
                triangles_3mf.append(Lib3MF.Triangle(c_array))
                # mesh_3mf.AddTriangle Should AddTriangle be used to save memory?

            # Create the mesh
            mesh_3mf.SetGeometry(vertices_3mf, triangles_3mf)

            # Add color
            if shape.color:
                color_group = self.model.AddColorGroup()
                color_index = color_group.AddColor(
                    self.wrapper.FloatRGBAToColor(*shape.color.to_tuple())
                )
                triangle_property = Lib3MF.TriangleProperties()
                triangle_property.ResourceID = color_group.GetResourceID()
                triangle_property.PropertyIDs[0] = color_index
                triangle_property.PropertyIDs[1] = color_index
                triangle_property.PropertyIDs[2] = color_index
                for i in range(mesh_3mf.GetTriangleCount()):
                    mesh_3mf.SetTriangleProperties(i, ctypes.pointer(triangle_property))

            # Object Level Property
            mesh_3mf.SetObjectLevelProperty(color_group.GetResourceID(), color_index)

            # Check mesh
            if not mesh_3mf.IsValid():
                raise RuntimeError("3mf mesh is invalid")
            if not mesh_3mf.IsManifoldAndOriented():
                warnings.warn("3mf mesh is not manifold")

            # Add mesh to model
            self.meshes.append(mesh_3mf)
            self.model.AddBuildItem(mesh_3mf, self.wrapper.GetIdentityTransform())

            # Not sure is this is required...
            components = self.model.AddComponentsObject()
            components.AddComponent(mesh_3mf, self.wrapper.GetIdentityTransform())

    def _get_shape(self, mesh_3mf: Lib3MF.MeshObject) -> Shape:
        """Build build123d object from lib3mf mesh"""
        # Extract all the vertices
        gp_pnts = [gp_Pnt(*p.Coordinates[0:3]) for p in mesh_3mf.GetVertices()]

        # Extract all the triangle and create a Shell from generated Faces
        shell_builder = BRepBuilderAPI_Sewing()
        for i in range(mesh_3mf.GetTriangleCount()):
            # Extract the vertex indices for this triangle
            tri_indices = mesh_3mf.GetTriangle(i).Indices[0:3]
            # Convert to a list of gp_Pnt
            ocp_vertices = [gp_pnts[tri_indices[i]] for i in range(3)]
            # Create the triangular face using the polygon
            polygon_builder = BRepBuilderAPI_MakePolygon(*ocp_vertices, Close=True)
            face_builder = BRepBuilderAPI_MakeFace(polygon_builder.Wire())
            # Add new Face to Shell
            shell_builder.Add(face_builder.Face())

        # Create the Shell
        shell_builder.Perform()
        occ_shell = downcast(shell_builder.SewedShape())

        # Create a solid if manifold
        shape_obj = Shell(occ_shell)
        if shape_obj.is_manifold:
            solid_builder = BRepBuilderAPI_MakeSolid(occ_shell)
            shape_obj = Solid(solid_builder.Solid())

        return shape_obj

    def read(self, file_name: str) -> list[Shape]:
        """read

        Args:
            file_name (str): file path

        Raises:
            ValueError: Unknown file format - must be 3mf or stl

        Returns:
            list[Shape]: build123d shapes extracted from mesh file
        """
        input_file_format = file_name.split(".")[-1].lower()
        if input_file_format not in ["3mf", "stl"]:
            raise ValueError(f"Unknown file format {input_file_format}")
        reader = self.model.QueryReader(input_file_format)
        reader.ReadFromFile(file_name)
        self.unit = Mesher.map_3mf_to_b3d_unit[self.model.GetUnit()]

        # Extract 3MF meshes and translate to OCP meshes
        mesh_iterator: Lib3MF.MeshObjectIterator = self.model.GetMeshObjects()
        self.meshes: list[Lib3MF.MeshObject]
        for _i in range(mesh_iterator.Count()):
            mesh_iterator.MoveNext()
            self.meshes.append(mesh_iterator.GetCurrentMeshObject())
        shapes = []
        for mesh in self.meshes:
            shape = self._get_shape(mesh)
            shape.label = mesh.GetName()
            triangle_properties = mesh.GetAllTriangleProperties()
            color_indices = []
            for triangle_property in triangle_properties:
                color_indices.extend(
                    [
                        (triangle_property.ResourceID, triangle_property.PropertyIDs[i])
                        for i in range(3)
                    ]
                )
            unique_color_indices = list(set(color_indices))
            if len(unique_color_indices) > 1:
                warnings.warn("Warning multiple colors found on mesh - one used")
            color_group = self.model.GetColorGroupByID(unique_color_indices[0][0])
            color_3mf = color_group.GetColor(unique_color_indices[0][1])
            color = (color_3mf.Red, color_3mf.Green, color_3mf.Blue, color_3mf.Alpha)
            color = (c / 255.0 for c in color)
            shape.color = Color(*color)
            shapes.append(shape)

        return shapes

    def write(self, file_name: str):
        """_summary_

        Args:
            file_name (str): file path

        Raises:
            ValueError: Unknown file format - must be 3mf or stl
        """
        output_file_format = file_name.split(".")[-1].lower()
        if output_file_format not in ["3mf", "stl"]:
            raise ValueError(f"Unknown file format {output_file_format}")
        writer = self.model.QueryWriter(output_file_format)
        writer.WriteToFile(file_name)