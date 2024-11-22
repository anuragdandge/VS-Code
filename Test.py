import pytesseract

pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'  # Example: r'C:\Program Files\Tesseract-OCR\tesseract.exe' on Windows
try:
    print(pytesseract.image_to_string('./zoomed_cropped_image.png'))  # Replace 'image.png' with an actual image file
except Exception as e:
    print(f"Error: {e}")