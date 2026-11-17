"""Bootstrap: creates all required directories."""
import os
dirs = ["handlers", "handlers/__pycache__"]
for d in dirs:
    os.makedirs(d, exist_ok=True)
print("Directories created.")
