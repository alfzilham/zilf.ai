import os

def fix_encoding(filepath):
    with open(filepath, 'r', encoding='windows-1252') as f:
        content = f.read()
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

# Jalankan untuk semua file .py di project
for root, dirs, files in os.walk('.'):
    # Skip folder yang tidak perlu
    dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', 'chroma_db']]
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            try:
                fix_encoding(filepath)
                print(f"Fixed: {filepath}")
            except Exception as e:
                print(f"Skip {filepath}: {e}")