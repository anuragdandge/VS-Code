import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import cv2
import numpy as np
import re
import os
import pandas as pd
from datetime import datetime
from flask import Flask, request, render_template, send_file, redirect, url_for
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # windows
pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract' # Linux 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

# Ensure the uploads directory exists
if not os.path.exists('uploads'):
    os.makedirs('uploads')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'pdf' not in request.files:
        return "No file part"
    file = request.files['pdf']
    if file.filename == '':
        return "No selected file"
    if file:
        filename = secure_filename(file.filename)
        upload_path = os.path.join('uploads', filename)
        file.save(upload_path)
        socketio.start_background_task(process_pdf, upload_path, coordinates)
        return redirect(url_for('index'))

def extract_text_from_region(pdf_path, coords, page_number, zoom_factor):
    # cv2.ocl.setUseOpenCL(True)  # GPU 
    pdf_document = fitz.open(pdf_path)
    page = pdf_document.load_page(page_number)
    x, y, width, height = coords
    rect = fitz.Rect(x, y, x + width, y + height)

    mat = fitz.Matrix(zoom_factor, zoom_factor)
    pix = page.get_pixmap(matrix=mat, clip=rect)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    kernel = np.ones((3, 3), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
    return pytesseract.image_to_string(thresh, config='--psm 6')

def check_regex(text, pattern=r"[A-Z]{3}/[A-Z]{2}\s?\d{4}/[A-Z]{2}/\d{7}"):
    match = re.search(pattern, text, re.MULTILINE)
    return bool(match), match.group(0) if match else None

def process_pdf(pdf_path, coordinates, zoom_factor=4, output_dir="output"):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "duplicate"), exist_ok=True)
    excel_path = os.path.join(output_dir, "pdf_processing_log.xlsx")

    try:
        df = pd.read_excel(excel_path)
    except FileNotFoundError:
        df = pd.DataFrame(columns=["PDF File", "Company Code", "Farmer Name", "Timestamp", "Status", "Error Description"])

    pdf_document = fitz.open(pdf_path)
    new_pdf = None
    current_pdf_name = None
    processed_pdf_names = set()
    last_farmer_name = None

    total_pages = pdf_document.page_count

    for page_number in range(total_pages):
        socketio.emit('update', {'current': page_number + 1, 'total': total_pages, 'status': 'Processing'}, namespace='/')
        print(f"Processing page {page_number + 1} of {total_pages}")
        matched_text = None

        for coord_set in coordinates:
            company_code_coords = coord_set["company_code"]
            farmer_name_coords = coord_set["farmer_name"]

            try:
                extracted_text = extract_text_from_region(pdf_path, company_code_coords, page_number, zoom_factor)
                is_match, matched_text = check_regex(extracted_text)

                if is_match:
                    farmer_name = extract_text_from_region(pdf_path, farmer_name_coords, page_number, zoom_factor)
                    farmer_name = farmer_name.strip()
                    break

            except Exception as e:
                print(f"Error processing coordinates: {e}")

        try:
            if matched_text:
                pdf_name = matched_text.replace(" ", "_").replace("/", "_")
                print(pdf_name)

                if pdf_name in processed_pdf_names:
                    output_subdir = os.path.join(output_dir, "duplicate")
                else:
                    output_subdir = output_dir
                    processed_pdf_names.add(pdf_name)

                if pdf_name != current_pdf_name:
                    if new_pdf:
                        new_pdf_path = os.path.join(output_dir, f"{current_pdf_name}.pdf")
                        new_pdf.save(new_pdf_path)
                        new_pdf.close()

                        df = pd.concat([df, pd.DataFrame({"PDF File": pdf_path, "Company Code": current_pdf_name, "Farmer Name": last_farmer_name,
                                                          "Timestamp": datetime.now(), "Status": "Success", "PDF Page No.": page_number+1}, index=[0])], ignore_index=True)

                    new_pdf = fitz.open()
                    current_pdf_name = pdf_name
                    last_farmer_name = farmer_name

                new_pdf.insert_pdf(pdf_document, from_page=page_number, to_page=page_number)

            elif new_pdf:
                new_pdf.insert_pdf(pdf_document, from_page=page_number, to_page=page_number)
                last_farmer_name = farmer_name

        except Exception as e:
            df = pd.concat([df, pd.DataFrame({"PDF File": pdf_path, "Company Code": "N/A", "Farmer Name": farmer_name,
                                              "Timestamp": datetime.now(), "Status": "Error", "Error Description": str(e)}, index=[0])], ignore_index=True)
            print(f"Error processing page {page_number + 1}: {e}")

    if new_pdf:
        new_pdf_path = os.path.join(output_dir if current_pdf_name not in processed_pdf_names else output_subdir, f"{current_pdf_name}.pdf")
        new_pdf.save(new_pdf_path)
        new_pdf.close()

        df = pd.concat([df, pd.DataFrame({"PDF File": pdf_path, "Company Code": current_pdf_name, "Farmer Name": last_farmer_name,
                                          "Timestamp": datetime.now(), "Status": "Success", "Error Description": ""}, index=[0])], ignore_index=True)

    df.to_excel(excel_path, index=False)
    socketio.emit('update', {'current': total_pages, 'total': total_pages, 'status': 'Complete', 'link': os.path.join('output', 'pdf_processing_log.xlsx')}, namespace='/')

# Define your coordinates (replace with your actual coordinates)
coordinates = [
    {"label": "Set1", "company_code": (412, 122, 127, 28), "farmer_name": (175, 209, 112, 21)}, 
    {"label": "Set2", "company_code": (417, 121, 114, 18), "farmer_name": (175, 209, 112, 21)}, 
    {"label": "Set3", "company_code": (421, 123, 116, 24), "farmer_name": (171, 209, 125, 17)}, 
    {"label": "Set4", "company_code": (422, 131, 110, 20), "farmer_name": (173, 220, 125, 16)}, 
    {"label": "Set5", "company_code": (425, 124, 112, 18), "farmer_name": (173, 211, 125, 18)}, 
    {"label": "Set6", "company_code": (426, 118, 114, 18), "farmer_name": (177, 209, 127, 19)}, 
    {"label": "Set7", "company_code": (427, 121, 114, 21), "farmer_name": (176, 209, 128, 18)}, 
    {"label": "Set8", "company_code": (427, 115, 114, 19), "farmer_name": (178, 203, 128, 18)}, 
    {"label": "Set9", "company_code": (430, 126, 115, 23), "farmer_name": (179, 211, 107, 19)},
]

if __name__ == '__main__':
    socketio.run(app, debug=True)
