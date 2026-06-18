#!/usr/bin/env python3
"""
gRPC Proto File Generator for V.Y.R.A. Modules

This tool generates Python code from .proto files located in storage/interfaces.
It's designed to be integrated into the CI/CD pipeline and Dockerfile build process.

Usage:
    python generate_grpc_protos.py [--proto-dir PATH] [--output-dir PATH]
    
Default behavior:
    - Searches for .proto files in: /workspace/storage/interfaces/
    - Generates Python code in: /workspace/src/rest_api/grpc_generated/
    
CI/CD Integration:
    Add to Dockerfile:
    ```dockerfile
    RUN python /workspace/tools/generate_grpc_protos.py
    ```
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GrpcProtoGenerator:
    """Generator for gRPC Python code from .proto files."""
    
    def __init__(
        self,
        proto_dir: Path,
        output_dir: Path,
        clean_output: bool = True
    ):
        """
        Initialize the proto generator.
        
        Args:
            proto_dir: Directory containing .proto files
            output_dir: Directory for generated Python code
            clean_output: Whether to clean output directory before generation
        """
        self.proto_dir = proto_dir
        self.output_dir = output_dir
        self.clean_output = clean_output
        
    def validate_directories(self) -> bool:
        """
        Validate that required directories exist.
        
        Returns:
            True if validation successful, False otherwise
        """
        if not self.proto_dir.exists():
            logger.error(f"❌ Proto directory not found: {self.proto_dir}")
            return False
            
        proto_files = list(self.proto_dir.glob("*.proto"))
        if not proto_files:
            logger.warning(f"⚠️ No .proto files found in {self.proto_dir}")
            return False
            
        logger.info(f"✅ Found {len(proto_files)} .proto file(s)")
        for proto_file in proto_files:
            logger.info(f"   📄 {proto_file.name}")
            
        return True
    
    def prepare_output_directory(self):
        """Create and optionally clean the output directory."""
        if self.clean_output and self.output_dir.exists():
            logger.info(f"🧹 Cleaning output directory: {self.output_dir}")
            # Remove all generated files
            for file in self.output_dir.glob("*_pb2*.py"):
                file.unlink()
                logger.debug(f"   Removed: {file.name}")
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📁 Output directory ready: {self.output_dir}")
        
        # Create __init__.py for Python package
        init_file = self.output_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text('"""Generated gRPC modules."""\n')
            logger.info(f"✅ Created {init_file.name}")
    
    def generate_proto(self, proto_file: Path) -> bool:
        """
        Generate Python code from a single .proto file.
        
        Args:
            proto_file: Path to .proto file
            
        Returns:
            True if generation successful, False otherwise
        """
        logger.info(f"🔧 Generating Python code for: {proto_file.name}")
        
        try:
            # Build protoc command with pyi_out for type hints
            cmd = [
                "python3", "-m", "grpc_tools.protoc",
                f"--proto_path={self.proto_dir}",
                f"--python_out={self.output_dir}",
                f"--grpc_python_out={self.output_dir}",
                f"--pyi_out={self.output_dir}",  # Generate .pyi files
                str(proto_file)
            ]
            
            logger.debug(f"Command: {' '.join(cmd)}")
            
            # Execute protoc
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            if result.stdout:
                logger.debug(f"stdout: {result.stdout}")
            if result.stderr:
                logger.debug(f"stderr: {result.stderr}")
                
            # Verify generated files
            base_name = proto_file.stem
            pb2_file = self.output_dir / f"{base_name}_pb2.py"
            grpc_file = self.output_dir / f"{base_name}_pb2_grpc.py"
            
            if pb2_file.exists() and grpc_file.exists():
                # Fix imports in generated grpc file (convert to relative imports)
                self._fix_grpc_imports(grpc_file, base_name)
                
                logger.info(f"✅ Generated:")
                logger.info(f"   • {pb2_file.name}")
                logger.info(f"   • {grpc_file.name}")
                return True
            else:
                logger.error(f"❌ Expected files not generated")
                return False
                
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ protoc failed for {proto_file.name}")
            logger.error(f"   Error: {e.stderr}")
            return False
        except Exception as e:
            logger.exception(f"❌ Unexpected error: {e}")
            return False
    
    def _fix_grpc_imports(self, grpc_file: Path, base_name: str):
        """Fix imports in generated grpc file to use relative imports."""
        content = grpc_file.read_text()
        
        # Replace absolute import with relative import
        # From: import permission_service_pb2 as permission__service__pb2
        # To:   from . import permission_service_pb2 as permission__service__pb2
        old_import = f"import {base_name}_pb2 as {base_name.replace('_', '__')}__pb2"
        new_import = f"from . import {base_name}_pb2 as {base_name.replace('_', '__')}__pb2"
        
        if old_import in content:
            content = content.replace(old_import, new_import)
            grpc_file.write_text(content)
            logger.debug(f"   Fixed imports in {grpc_file.name}")
    
    def generate_all(self) -> bool:
        """
        Generate Python code for all .proto files.
        
        Returns:
            True if all generations successful, False otherwise
        """
        logger.info("🚀 Starting gRPC proto generation...")
        
        # Validate
        if not self.validate_directories():
            return False
        
        # Prepare output
        self.prepare_output_directory()
        
        # Generate for each proto file
        proto_files = list(self.proto_dir.glob("*.proto"))
        success_count = 0
        
        for proto_file in proto_files:
            if self.generate_proto(proto_file):
                success_count += 1
                # Generate type stubs
                self.generate_type_stubs(proto_file)
        
        # Summary
        total = len(proto_files)
        logger.info(f"\n{'='*60}")
        logger.info(f"✨ Generation complete: {success_count}/{total} successful")
        logger.info(f"{'='*60}")
        
        return success_count == total
    
    def generate_type_stubs(self, proto_file: Path) -> bool:
        """
        Generate type stub files (.pyi) for better IDE support.
        
        Args:
            proto_file: Path to .proto file
            
        Returns:
            True if generation successful, False otherwise
        """
        base_name = proto_file.stem
        logger.info(f"📝 Generating type stubs for: {base_name}")
        
        try:
            # Parse proto file to extract message and service definitions
            messages, services = self._parse_proto_file(proto_file)
            
            # Generate _pb2.pyi (message types)
            pb2_stub = self._generate_pb2_stub(base_name, messages)
            pb2_stub_file = self.output_dir / f"{base_name}_pb2.pyi"
            pb2_stub_file.write_text(pb2_stub)
            logger.info(f"   ✅ Created {pb2_stub_file.name}")
            
            # Generate _pb2_grpc.pyi (service stubs)
            if services:
                grpc_stub = self._generate_grpc_stub(base_name, services, messages)
                grpc_stub_file = self.output_dir / f"{base_name}_pb2_grpc.pyi"
                grpc_stub_file.write_text(grpc_stub)
                logger.info(f"   ✅ Created {grpc_stub_file.name}")
            
            return True
            
        except Exception as e:
            logger.exception(f"❌ Failed to generate type stubs: {e}")
            return False
    
    def _parse_proto_file(self, proto_file: Path) -> tuple[list[dict], list[dict]]:
        """Parse proto file to extract message and service definitions."""
        content = proto_file.read_text()
        messages = []
        services = []
        
        # Simple regex-based parsing (good enough for basic cases)
        import re
        
        # Extract messages
        message_pattern = r'message\s+(\w+)\s*\{([^}]+)\}'
        for match in re.finditer(message_pattern, content):
            msg_name = match.group(1)
            msg_body = match.group(2)
            
            # Extract fields
            field_pattern = r'(repeated\s+)?(\w+)\s+(\w+)\s*=\s*\d+;'
            fields = []
            for field_match in re.finditer(field_pattern, msg_body):
                is_repeated = field_match.group(1) is not None
                field_type = field_match.group(2)
                field_name = field_match.group(3)
                fields.append({
                    'name': field_name,
                    'type': field_type,
                    'repeated': is_repeated
                })
            
            messages.append({
                'name': msg_name,
                'fields': fields
            })
        
        # Extract services
        service_pattern = r'service\s+(\w+)\s*\{([^}]+)\}'
        for match in re.finditer(service_pattern, content):
            service_name = match.group(1)
            service_body = match.group(2)
            
            # Extract RPC methods
            rpc_pattern = r'rpc\s+(\w+)\s*\((\w+)\)\s*returns\s*\((\w+)\)'
            methods = []
            for rpc_match in re.finditer(rpc_pattern, service_body):
                methods.append({
                    'name': rpc_match.group(1),
                    'request': rpc_match.group(2),
                    'response': rpc_match.group(3)
                })
            
            services.append({
                'name': service_name,
                'methods': methods
            })
        
        return messages, services
    
    def _generate_pb2_stub(self, base_name: str, messages: list[dict]) -> str:
        """Generate _pb2.pyi stub file content."""
        stub = f'''"""Type stubs for {base_name}_pb2 module."""
from typing import Iterable, Optional
from google.protobuf.message import Message

'''
        
        for msg in messages:
            stub += f'''class {msg['name']}(Message):
'''
            # Add field annotations
            for field in msg['fields']:
                py_type = self._proto_type_to_python(field['type'], field['repeated'])
                stub += f"    {field['name']}: {py_type}\n"
            
            # Add __init__ method
            stub += "    \n    def __init__(\n        self,\n"
            for field in msg['fields']:
                py_type = self._proto_type_to_python(field['type'], field['repeated'], for_init=True)
                stub += f"        {field['name']}: {py_type} = ...,\n"
            stub += "    ) -> None: ...\n\n"
        
        return stub
    
    def _generate_grpc_stub(self, base_name: str, services: list[dict], messages: list[dict]) -> str:
        """Generate _pb2_grpc.pyi stub file content."""
        stub = f'''"""Type stubs for {base_name}_pb2_grpc module."""
from typing import Optional, Union
import grpc
import grpc.aio
from grpc.aio import UnaryUnaryCall
from . import {base_name}_pb2

'''
        
        for service in services:
            service_name = service['name']
            
            # Generate Stub class (client)
            stub += f'''class {service_name}Stub:
    def __init__(self, channel: Union[grpc.Channel, grpc.aio.Channel]) -> None: ...
    
'''
            for method in service['methods']:
                stub += f'''    def {method['name']}(
        self,
        request: {base_name}_pb2.{method['request']},
        timeout: Optional[float] = ...,
        metadata: Optional[grpc.Metadata] = ...,
        credentials: Optional[grpc.CallCredentials] = ...,
    ) -> UnaryUnaryCall[{base_name}_pb2.{method['response']}]: ...
    
'''
            
            # Generate Servicer class (server)
            stub += f'''class {service_name}Servicer:
'''
            for method in service['methods']:
                stub += f'''    async def {method['name']}(
        self,
        request: {base_name}_pb2.{method['request']},
        context: grpc.ServicerContext,
    ) -> {base_name}_pb2.{method['response']}: ...
    
'''
            
            # Generate add_servicer_to_server function
            stub += f'''def add_{service_name}Servicer_to_server(
    servicer: {service_name}Servicer,
    server: grpc.Server,
) -> None: ...
'''
        
        return stub
    
    def _proto_type_to_python(self, proto_type: str, repeated: bool = False, for_init: bool = False) -> str:
        """Convert protobuf type to Python type annotation."""
        type_map = {
            'string': 'str',
            'int32': 'int',
            'int64': 'int',
            'uint32': 'int',
            'uint64': 'int',
            'bool': 'bool',
            'float': 'float',
            'double': 'float',
            'bytes': 'bytes',
        }
        
        py_type = type_map.get(proto_type, proto_type)
        
        if repeated:
            if for_init:
                return f"Optional[Iterable[{py_type}]]"
            return f"list[{py_type}]"
        
        if for_init:
            return py_type
        
        return py_type
    
    def create_client_helpers(self):
        """
        Create helper file for easy client access.
        
        This creates a clients.py file that provides convenient access
        to all generated stubs.
        """
        logger.info("📝 Creating client helper file...")
        
        helper_content = '''"""
Auto-generated client helpers for gRPC services.

This module provides convenient access to gRPC client stubs.

Usage:
    from rest_api.grpc_generated.clients import get_permission_client
    
    client = await get_permission_client()
    response = await client.RegisterPermission(request)
"""

import logging
from pathlib import Path
from vyra_base.com.handler import GrpcClient

logger = logging.getLogger(__name__)

# Default socket directory
SOCKET_DIR = Path("/tmp/vyra_sockets")
SOCKET_DIR.mkdir(parents=True, exist_ok=True)

'''
        
        # Add imports and client factories for each service
        proto_files = list(self.proto_dir.glob("*.proto"))
        
        for proto_file in proto_files:
            base_name = proto_file.stem
            module_name = f"{base_name}_pb2_grpc"
            
            # Parse service name from proto file
            service_names = self._extract_service_names(proto_file)
            
            for service_name in service_names:
                stub_name = f"{service_name}Stub"
                client_func_name = f"get_{base_name.lower()}_client"
                socket_path = f"vyra_{base_name.lower()}.sock"
                
                helper_content += f'''
# {service_name} Client Factory
async def {client_func_name}(socket_path: str = None):
    """
    Get {service_name} client.
    
    Args:
        socket_path: Custom socket path (default: /tmp/vyra_sockets/{socket_path})
        
    Returns:
        Connected gRPC client with {stub_name}
    """
    from . import {module_name}
    
    if socket_path is None:
        socket_path = SOCKET_DIR / "{socket_path}"
    
    client = GrpcClient(socket_path)
    await client.connect()
    
    # Create stub
    client.stub = {module_name}.{stub_name}(client.channel)
    
    logger.info(f"🔗 Connected to {% raw %}{{socket_path}}{% endraw %}")
    return client

'''
        
        # Write helper file
        helper_file = self.output_dir / "clients.py"
        helper_file.write_text(helper_content)
        logger.info(f"✅ Created {helper_file.name}")
    
    def _extract_service_names(self, proto_file: Path) -> list[str]:
        """
        Extract service names from .proto file.
        
        Args:
            proto_file: Path to .proto file
            
        Returns:
            List of service names
        """
        services = []
        content = proto_file.read_text()
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('service '):
                # Extract service name
                parts = line.split()
                if len(parts) >= 2:
                    service_name = parts[1].rstrip('{')
                    services.append(service_name)
        
        return services


def main():
    """Main entry point for proto generation."""
    parser = argparse.ArgumentParser(
        description="Generate Python gRPC code from .proto files"
    )
    parser.add_argument(
        "--proto-dir",
        type=Path,
        default=Path("/workspace/storage/interfaces"),
        help="Directory containing .proto files (default: /workspace/storage/interfaces)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/workspace/storage/interfaces/grpc_generated"),
        help="Output directory for generated Python code (default: /workspace/storage/grpc_generated)"
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Don't clean output directory before generation"
    )
    parser.add_argument(
        "--create-helpers",
        action="store_true",
        default=True,
        help="Create client helper file (default: True)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Create generator
    generator = GrpcProtoGenerator(
        proto_dir=args.proto_dir,
        output_dir=args.output_dir,
        clean_output=not args.no_clean
    )
    
    # Generate proto files
    success = generator.generate_all()
    
    # Create helper files
    if success and args.create_helpers:
        generator.create_client_helpers()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
