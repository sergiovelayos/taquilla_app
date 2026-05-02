import pdfplumber
def read():
    with pdfplumber.open("scraper_icaa/158720.pdf") as pdf:
        text = "\n".join([page.extract_text() for page in pdf.pages])
        print(text)
if __name__ == '__main__':
    read()
