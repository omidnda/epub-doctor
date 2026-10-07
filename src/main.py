import sys
from pprint import pprint

from .service import repair_epub_file


def main():
    if len(sys.argv) != 3:
        print("Usage: python main.py <input.epub> <output.epub>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    result = repair_epub_file(input_path, output_path)

    pprint(result)


if __name__ == "__main__":
    main()
