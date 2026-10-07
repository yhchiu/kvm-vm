# pytest configuration for kvm-vm tests.


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests requiring real KVM/libvirt host"
    )
