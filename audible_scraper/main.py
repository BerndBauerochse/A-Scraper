import sys
from PySide6.QtWidgets import QApplication
from .gui import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    
    # Auto-refresh removed as per user request
    # window.start_refresh()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
