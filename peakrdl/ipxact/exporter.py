from typing import Union, Optional, TYPE_CHECKING, Any
import enum

from xml.dom import minidom
from systemrdl.node import AddressableNode, RootNode, Node
from systemrdl.node import AddrmapNode, MemNode
from systemrdl.node import RegNode, RegfileNode, FieldNode

from . import typemaps

if TYPE_CHECKING:
    from systemrdl.messages import MessageHandler

class Standard(enum.IntEnum):
    """
    Enumeration of IP-XACT standards
    """

    #: Spirit IP-XACT - IEEE Std. 1685-2009
    IEEE_1685_2009 = 2009

    #: IP-XACT - IEEE Std. 1685-2014
    IEEE_1685_2014 = 2014

#===============================================================================
class IPXACTExporter:
    def __init__(self, **kwargs: Any) -> None:
        """
        Constructor for the exporter object.

        Parameters
        ----------
        vendor: str
            Vendor url string. Defaults to "example.org"
        library: str
            library name string. Defaults to "mylibrary"
        version: str
            Version string. Defaults to "1.0"
        standard: :class:`Standard`
            IP-XACT Standard to use. Currently supports IEEE 1685-2009 and
            IEEE 1685-2014 (default)
        xml_indent: str
            String to use for each indent level. Defaults to 2 spaces.
        xml_newline: str
            String to use for line breaks. Defaults to a newline (``\\n``).
        """

        self.msg = None # type: MessageHandler

        self.vendor = kwargs.pop("vendor", "example.org")
        self.library = kwargs.pop("library", "mylibrary")
        self.version = kwargs.pop("version", "1.0")
        self.standard = kwargs.pop("standard", Standard.IEEE_1685_2014)
        self.xml_indent = kwargs.pop("xml_indent", "  ")
        self.xml_newline = kwargs.pop("xml_newline", "\n")
        self.doc = None # type: minidom.Document
        self._max_width = None # type: Optional[int]

        # Check for stray kwargs
        if kwargs:
            raise TypeError("got an unexpected keyword argument '%s'" % list(kwargs.keys())[0])

        if self.standard >= Standard.IEEE_1685_2014:
            self.ns = "ipxact:"
        else:
            self.ns = "spirit:"

    #---------------------------------------------------------------------------
    def export(self, node: Union[AddrmapNode, RootNode], path: str, **kwargs: Any) -> None:
        """
        Parameters
        ----------
        node: AddrmapNode
            Top-level SystemRDL node to export.
        path:
            Path to save the exported XML file.
        component_name: str
            IP-XACT component name. If unspecified, uses the top node's name
            upon export.
        """

        self.msg = node.env.msg

        component_name = kwargs.pop("component_name", None)

        # Check for stray kwargs
        if kwargs:
            raise TypeError("got an unexpected keyword argument '%s'" % list(kwargs.keys())[0])

        # If it is the root node, skip to top addrmap
        if isinstance(node, RootNode):
            node = node.top

        if not isinstance(node, (AddrmapNode, MemNode)):
            raise TypeError("'node' argument expects type AddrmapNode or MemNode. Got '%s'" % type(node).__name__)

        if isinstance(node, AddrmapNode) and node.get_property('bridge'):
            self.msg.warning(
                "IP-XACT generator does not have proper support for bridge addmaps yet. The 'bridge' property will be ignored.",
                node.inst.property_src_ref.get('bridge', node.inst.inst_src_ref)
            )

        # Initialize XML DOM
        self.doc = minidom.getDOMImplementation().createDocument(None, None, None)

        tmp = self.doc.createComment("Generated by PeakRDL IP-XACT (https://github.com/SystemRDL/PeakRDL-ipxact)")
        self.doc.appendChild(tmp)

        # Create top-level component
        comp = self.doc.createElement(self.ns + "component")
        if self.standard == Standard.IEEE_1685_2014:
            comp.setAttribute("xmlns:ipxact", "http://www.accellera.org/XMLSchema/IPXACT/1685-2014")
            comp.setAttribute("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
            comp.setAttribute("xsi:schemaLocation", "http://www.accellera.org/XMLSchema/IPXACT/1685-2014 http://www.accellera.org/XMLSchema/IPXACT/1685-2014/index.xsd")
        elif self.standard == Standard.IEEE_1685_2009:
            comp.setAttribute("xmlns:spirit", "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009")
            comp.setAttribute("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
            comp.setAttribute("xsi:schemaLocation", "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009 http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009/index.xsd")

        else:
            raise RuntimeError
        self.doc.appendChild(comp)

        # versionedIdentifier Block
        self.add_value(comp, self.ns + "vendor", self.vendor)
        self.add_value(comp, self.ns + "library", self.library)
        self.add_value(comp, self.ns + "name", component_name or node.inst_name)
        self.add_value(comp, self.ns + "version", self.version)

        mmaps = self.doc.createElement(self.ns + "memoryMaps")
        comp.appendChild(mmaps)

        # Determine if top-level node should be exploded across multiple
        # addressBlock groups
        explode = False

        # If top node is an addrmap, and it contains 1 or more children that
        # are:
        # - exclusively addrmap or mem
        # - and None of them are arrays
        # ... then it makes more sense to "explode" the
        # top-level node and make each of it's children their own addressBlock
        # (explode --> True)
        #
        # Otherwise, do not "explode" the top-level node
        # (explode --> False)
        if isinstance(node, AddrmapNode):
            addrblockable_children = 0
            non_addrblockable_children = 0

            for child in node.children(skip_not_present=False):
                if not isinstance(child, AddressableNode):
                    continue

                if isinstance(child, (AddrmapNode, MemNode)) and not child.is_array:
                    addrblockable_children += 1
                else:
                    non_addrblockable_children += 1

            if (non_addrblockable_children == 0) and (addrblockable_children >= 1):
                explode = True

        # Do the export!
        if explode:
            # top-node becomes the memoryMap
            mmap = self.doc.createElement(self.ns + "memoryMap")
            self.add_nameGroup(mmap,
                node.inst_name,
                node.get_property("name", default=None),
                node.get_property("desc")
            )
            mmaps.appendChild(mmap)

            # Top-node's children become their own addressBlocks
            for child in node.children(skip_not_present=False):
                if not isinstance(child, AddressableNode):
                    continue

                self.add_addressBlock(mmap, child)
        else:
            # Not exploding apart the top-level node

            # Wrap it in a dummy memoryMap that bears it's name
            mmap = self.doc.createElement(self.ns + "memoryMap")
            self.add_nameGroup(mmap, node.inst_name)
            mmaps.appendChild(mmap)

            # Export top-level node as a single addressBlock
            self.add_addressBlock(mmap, node)

        # Write out XML dom
        with open(path, "w", encoding='utf-8') as f:
            self.doc.writexml(
                f,
                addindent=self.xml_indent,
                newl=self.xml_newline,
                encoding="UTF-8"
            )

    #---------------------------------------------------------------------------
    def add_value(self, parent: minidom.Element, tag: str, value: str) -> None:
        el = self.doc.createElement(tag)
        txt = self.doc.createTextNode(value)
        el.appendChild(txt)
        parent.appendChild(el)

    #---------------------------------------------------------------------------
    def add_nameGroup(self, parent: minidom.Element, name: str, displayName: Optional[str]=None, description: Optional[str]=None) -> None:
        self.add_value(parent, self.ns + "name", name)
        if displayName is not None:
            self.add_value(parent, self.ns + "displayName", displayName)
        if description is not None:
            self.add_value(parent, self.ns + "description", description)

    #---------------------------------------------------------------------------
    def add_registerData(self, parent: minidom.Element, node: RegNode) -> None:
        if self.standard == Standard.IEEE_1685_2014:
            # registers and registerFiles can be interleaved
            for child in node.children(skip_not_present=False):
                if isinstance(child, RegNode):
                    self.add_register(parent, child)
                elif isinstance(child, (AddrmapNode, RegfileNode)):
                    self.add_registerFile(parent, child)
                elif isinstance(child, MemNode):
                    self.msg.warning(
                        "IP-XACT does not support 'mem' nodes that are nested in hierarchy. Discarding '%s'"
                        % child.get_path(),
                        child.inst.inst_src_ref
                    )
        elif self.standard == Standard.IEEE_1685_2009:
            # registers must all be listed before register files
            for child in node.children(skip_not_present=False):
                if isinstance(child, RegNode):
                    self.add_register(parent, child)

            for child in node.children(skip_not_present=False):
                if isinstance(child, (AddrmapNode, RegfileNode)):
                    self.add_registerFile(parent, child)
                elif isinstance(child, MemNode):
                    self.msg.warning(
                        "IP-XACT does not support 'mem' nodes that are nested in hierarchy. Discarding '%s'"
                        % child.get_path(),
                        child.inst.inst_src_ref
                    )
        else:
            raise RuntimeError

    #---------------------------------------------------------------------------
    def hex_str(self, v: int) -> str:
        if self.standard >= Standard.IEEE_1685_2014:
            return "'h%x" % v
        else:
            return "0x%x" % v

    #---------------------------------------------------------------------------
    def get_name(self, node: Node) -> str:
        return node.inst_name

    def get_reg_addr_offset(self, node: AddressableNode) -> int:
        return node.raw_address_offset

    def get_regfile_addr_offset(self, node: AddressableNode) -> int:
        return node.raw_address_offset

    #---------------------------------------------------------------------------
    def add_addressBlock(self, parent: minidom.Element, node: AddressableNode) -> None:
        self._max_width = None

        addressBlock = self.doc.createElement(self.ns + "addressBlock")
        parent.appendChild(addressBlock)

        self.add_nameGroup(addressBlock,
            self.get_name(node),
            node.get_property("name", default=None),
            node.get_property("desc")
        )

        if (self.standard >= Standard.IEEE_1685_2014) and not node.get_property("ispresent"):
            self.add_value(addressBlock, self.ns + "isPresent", "0")

        self.add_value(addressBlock, self.ns + "baseAddress", self.hex_str(node.absolute_address))

        # DNE: <spirit/ipxact:typeIdentifier>

        self.add_value(addressBlock, self.ns + "range", self.hex_str(node.size))

        # RDL only encodes the bus-width at the register level, but IP-XACT
        # only encodes this at the addressBlock level!
        # Insert the width element for now, but leave contents blank until it is
        # determined later.
        # Exporter has no choice but to enforce a constant width throughout
        width_el = self.doc.createElement(self.ns + "width")
        addressBlock.appendChild(width_el)

        if isinstance(node, MemNode):
            self.add_value(addressBlock, self.ns + "usage", "memory")
            access = typemaps.access_from_sw(node.get_property("sw"))
            self.add_value(addressBlock, self.ns + "access", access)

        # DNE: <spirit/ipxact:volatile>
        # DNE: <spirit/ipxact:access>
        # DNE: <spirit/ipxact:parameters>

        self.add_registerData(addressBlock, node)

        # Width should be known by now
        # If mem, and width isn't known, check memwidth
        if isinstance(node, MemNode) and (self._max_width is None):
            self._max_width = node.get_property("memwidth")

        if self._max_width is not None:
            width_el.appendChild(self.doc.createTextNode("%d" % self._max_width))
        else:
            width_el.appendChild(self.doc.createTextNode("32"))

        vendorExtensions = self.doc.createElement(self.ns + "vendorExtensions")
        self.addressBlock_vendorExtensions(vendorExtensions, node)
        if vendorExtensions.hasChildNodes():
            addressBlock.appendChild(vendorExtensions)

    #---------------------------------------------------------------------------
    def add_registerFile(self, parent: minidom.Element, node: Union[RegfileNode, AddrmapNode]) -> None:
        registerFile = self.doc.createElement(self.ns + "registerFile")
        parent.appendChild(registerFile)

        self.add_nameGroup(registerFile,
            self.get_name(node),
            node.get_property("name", default=None),
            node.get_property("desc")
        )

        if (self.standard >= Standard.IEEE_1685_2014) and not node.get_property("ispresent"):
            self.add_value(registerFile, self.ns + "isPresent", "0")

        if node.is_array:
            for dim in node.array_dimensions:
                self.add_value(registerFile, self.ns + "dim", "%d" % dim)

        self.add_value(registerFile, self.ns + "addressOffset", self.hex_str(self.get_regfile_addr_offset(node)))

        # DNE: <spirit/ipxact:typeIdentifier>

        if node.is_array:
            # For arrays, ipxact:range also defines the increment between indexes
            # Must use stride instead
            self.add_value(registerFile, self.ns + "range", self.hex_str(node.array_stride))
        else:
            self.add_value(registerFile, self.ns + "range", self.hex_str(node.size))

        self.add_registerData(registerFile, node)

        # DNE: <spirit/ipxact:parameters>

        vendorExtensions = self.doc.createElement(self.ns + "vendorExtensions")
        self.registerFile_vendorExtensions(vendorExtensions, node)
        if vendorExtensions.hasChildNodes():
            registerFile.appendChild(vendorExtensions)

    #---------------------------------------------------------------------------
    def add_register(self, parent: minidom.Element, node: RegNode) -> None:
        register = self.doc.createElement(self.ns + "register")
        parent.appendChild(register)

        self.add_nameGroup(register,
            self.get_name(node),
            node.get_property("name", default=None),
            node.get_property("desc")
        )

        if (self.standard >= Standard.IEEE_1685_2014) and not node.get_property("ispresent"):
            self.add_value(register, self.ns + "isPresent", "0")

        if node.is_array:
            if node.array_stride != (node.get_property("regwidth") / 8):
                self.msg.fatal(
                    "IP-XACT does not support register arrays whose stride is larger then the register's size",
                    node.inst.inst_src_ref
                )
            for dim in node.array_dimensions:
                self.add_value(register, self.ns + "dim", "%d" % dim)

        self.add_value(register, self.ns + "addressOffset", self.hex_str(self.get_reg_addr_offset(node)))

        # DNE: <spirit/ipxact:typeIdentifier>

        self.add_value(register, self.ns + "size", "%d" % node.get_property("regwidth"))

        if self._max_width is None:
            self._max_width = node.get_property("regwidth")
        else:
            self._max_width = max(node.get_property("regwidth"), self._max_width)

        # DNE: <spirit/ipxact:volatile>
        # DNE: <spirit/ipxact:access>

        if self.standard <= Standard.IEEE_1685_2009:
            reset = 0
            mask = 0
            for field in node.fields(skip_not_present=False):
                field_reset = field.get_property("reset")
                if field_reset is not None:
                    field_mask = ((1 << field.width) - 1) << field.lsb
                    field_reset = (field_reset << field.lsb) & field_mask
                    reset |= field_reset
                    mask |= field_mask

            if mask != 0:
                reset_el = self.doc.createElement(self.ns + "reset")
                register.appendChild(reset_el)
                self.add_value(reset_el, self.ns + "value", self.hex_str(reset))
                self.add_value(reset_el, self.ns + "mask", self.hex_str(mask))

        for field in node.fields(skip_not_present=False):
            self.add_field(register, field)

        # DNE: <spirit/ipxact:alternateRegisters> [...]
        # DNE: <spirit/ipxact:parameters>

        vendorExtensions = self.doc.createElement(self.ns + "vendorExtensions")
        self.register_vendorExtensions(vendorExtensions, node)
        if vendorExtensions.hasChildNodes():
            register.appendChild(vendorExtensions)

    #---------------------------------------------------------------------------
    def add_field(self, parent: minidom.Element, node: FieldNode) -> None:
        field = self.doc.createElement(self.ns + "field")
        parent.appendChild(field)

        self.add_nameGroup(field,
            self.get_name(node),
            node.get_property("name", default=None),
            node.get_property("desc")
        )

        if (self.standard >= Standard.IEEE_1685_2014) and not node.get_property("ispresent"):
            self.add_value(field, self.ns + "isPresent", "0")

        self.add_value(field, self.ns + "bitOffset", "%d" % node.low)

        if self.standard >= Standard.IEEE_1685_2014:
            reset = node.get_property("reset")
            if reset is not None:
                resets_el = self.doc.createElement(self.ns + "resets")
                field.appendChild(resets_el)
                reset_el = self.doc.createElement(self.ns + "reset")
                resets_el.appendChild(reset_el)
                self.add_value(reset_el, self.ns + "value", self.hex_str(reset))

        # DNE: <spirit/ipxact:typeIdentifier>

        self.add_value(field, self.ns + "bitWidth", "%d" % node.width)

        if node.is_volatile:
            self.add_value(field, self.ns + "volatile", "true")

        sw = node.get_property("sw")
        self.add_value(
            field,
            self.ns + "access",
            typemaps.access_from_sw(sw)
        )

        encode = node.get_property("encode")
        if encode is not None:
            enum_values_el = self.doc.createElement(self.ns + "enumeratedValues")
            field.appendChild(enum_values_el)
            for enum_value in encode:
                enum_value_el = self.doc.createElement(self.ns + "enumeratedValue")
                enum_values_el.appendChild(enum_value_el)
                self.add_nameGroup(enum_value_el,
                    enum_value.name,
                    enum_value.rdl_name,
                    enum_value.rdl_desc
                )
                self.add_value(enum_value_el, self.ns + "value", self.hex_str(enum_value.value))
                # DNE <spirit/ipxact:vendorExtensions>

        onwrite = node.get_property("onwrite")
        if onwrite:
            self.add_value(
                field,
                self.ns + "modifiedWriteValue",
                typemaps.mwv_from_onwrite(onwrite)
            )

        # DNE: <spirit/ipxact:writeValueConstraint>

        onread = node.get_property("onread")
        if onread:
            self.add_value(
                field,
                self.ns + "readAction",
                typemaps.readaction_from_onread(onread)
            )

        if node.get_property("donttest"):
            self.add_value(field, self.ns + "testable", "false")

        # DNE: <ipxact:reserved>

        # DNE: <spirit/ipxact:parameters>

        vendorExtensions = self.doc.createElement(self.ns + "vendorExtensions")
        self.field_vendorExtensions(vendorExtensions, node)
        if vendorExtensions.hasChildNodes():
            field.appendChild(vendorExtensions)

    #---------------------------------------------------------------------------
    def addressBlock_vendorExtensions(self, parent:minidom.Element, node:AddressableNode) -> None:
        pass

    def registerFile_vendorExtensions(self, parent:minidom.Element, node:AddressableNode) -> None:
        pass

    def register_vendorExtensions(self, parent:minidom.Element, node:RegNode) -> None:
        pass

    def field_vendorExtensions(self, parent:minidom.Element, node:FieldNode) -> None:
        pass
