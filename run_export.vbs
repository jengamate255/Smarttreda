Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "E:\Project\Mt5 Ea"
shell.Run """E:\Project\Mt5 Ea\.venv\Scripts\pythonw.exe"" -u -B ""E:\Project\Mt5 Ea\export_data_for_colab.py""", 0, False
