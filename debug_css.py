import re
import sys

file_path = r'd:\2026\Workspace\Website\.hams.ai\agent\static\chat.css'
with open(file_path, 'r', encoding='utf-8') as f:
    css = f.read()

print("--- TOAST CSS ---")
for match in re.finditer(r'#toast[^{]*\{[^}]+\}', css):
    print(match.group(0))

print("\n--- ATTACHMENT CHIP CSS ---")
for match in re.finditer(r'\.attachment-chip[^{]*\{[^}]+\}', css):
    print(match.group(0))

