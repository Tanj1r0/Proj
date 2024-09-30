import serial
import logging
from modbus_tk import modbus_rtu
from modbus_tk.defines import READ_HOLDING_REGISTERS, WRITE_SINGLE_REGISTER
import time
import threading
import signal
import sys
import json
import smtplib
from email.mime.text import MIMEText
from flask import Flask, jsonify
from logging.handlers import RotatingFileHandler

# Настройка логирования с ротацией логов
log_handler = RotatingFileHandler('bzk03_log.log', maxBytes=5000000, backupCount=5)
logging.basicConfig(handlers=[log_handler], level=logging.INFO)

app = Flask(__name__)

class BZK03:
    def __init__(self, port_rs485, port_usb, baudrate=9600):
        try:
            self.port_rs485 = serial.Serial(port_rs485, baudrate, timeout=1)
            logging.info(f"Порт RS-485 {port_rs485} открыт.")
        except serial.SerialException as e:
            logging.error(f"Ошибка при открытии порта RS-485 {port_rs485}: {e}")
            raise RuntimeError(f"Не удалось открыть порт RS-485 {port_rs485}: {e}")
        
        try:
            self.port_usb = serial.Serial(port_usb, baudrate, timeout=1)
            logging.info(f"Порт USB {port_usb} открыт.")
        except serial.SerialException as e:
            logging.error(f"Ошибка при открытии порта USB {port_usb}: {e}")
            self.port_rs485.close()
            raise RuntimeError(f"Не удалось открыть порт USB {port_usb}: {e}")
        
        try:
            self.master = modbus_rtu.RtuMaster(self.port_rs485)
            self.master.set_timeout(1.0)
            logging.info("Modbus RTU мастер инициализирован.")
        except Exception as e:
            logging.error(f"Ошибка инициализации Modbus: {e}")
            self.close()
            raise RuntimeError("Ошибка инициализации Modbus.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if self.port_rs485.is_open:
            self.port_rs485.close()
            logging.info("Порт RS-485 закрыт.")
        if self.port_usb.is_open:
            self.port_usb.close()
            logging.info("Порт USB закрыт.")

    def check_ports(self):
        if not self.port_rs485.is_open:
            try:
                self.port_rs485.open()
                logging.info("Порт RS-485 успешно переоткрыт.")
            except serial.SerialException as e:
                logging.error(f"Ошибка при переоткрытии RS-485: {e}")

        if not self.port_usb.is_open:
            try:
                self.port_usb.open()
                logging.info("Порт USB успешно переоткрыт.")
            except serial.SerialException as e:
                logging.error(f"Ошибка при переоткрытии USB: {e}")

    def retry_operation(self, func, max_retries=3, exit_on_error = False):
        retries = 0
        while retries < max_retries:
            try:
                return func()
            except Exception as e:
                retries += 1
                logging.error(f"Ошибка: {e}. Попытка {retries}/{max_retries}.")
                if exit_on_error:
                    break
                time.sleep(2)
        raise RuntimeError("Не удалось выполнить операцию после нескольких попыток.")

    def read_data(self):
        return self.retry_operation(lambda: self.master.execute(1, READ_HOLDING_REGISTERS, 0, 4))

    def write_settings(self, register, value):
        return self.retry_operation(lambda: self.master.execute(1, WRITE_SINGLE_REGISTER, register, output_value=value))

    def log_event(self, event):
        logging.info(f"Событие: {event}")
        if "ошибка" in event.lower():
            self.send_error_notification("Критическая ошибка в устройстве", event)

    def send_error_notification(self, subject, message):
        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From'] = 'your_email@example.com'
        msg['To'] = 'admin@example.com'

        with smtplib.SMTP('smtp.example.com') as server:
            server.login('your_email@example.com', 'password')
            server.sendmail('your_email@example.com', ['admin@example.com'], msg.as_string())

    def oscilloscope_record(self):
        logging.info("Запись осциллограмм начата.")

    def self_test(self):
        logging.info("Запущен тест устройства.")
        self.check_ports()

    def usb_read_settings(self):
        try:
            if self.port_usb.is_open:
                data = self.port_usb.read(100)
                logging.info(f"Прочитаны данные по USB: {data}")
        except serial.SerialException as e:
            logging.error(f"Ошибка при чтении по USB: {e}")

    def usb_write_settings(self, settings):
        try:
            if self.port_usb.is_open:
                self.port_usb.write(settings.encode())
                logging.info(f"Записаны настройки по USB: {settings}")
        except serial.SerialException as e:
            logging.error(f"Ошибка при записи по USB: {e}")

@app.route('/status')
def status():
    global device
    try:
        data = device.read_data()
        return jsonify({
            "rs485_port": device.port_rs485.is_open,
            "usb_port": device.port_usb.is_open,
            "last_data": data
        })
    except Exception as e:
        logging.error(f"Ошибка получения статуса: {e}")
        return jsonify({"error": "Невозможно получить данные"}), 500

# Загрузка конфигурации из файла
def load_config(filename):
    with open(filename, 'r') as f:
        config = json.load(f)
    return config

# Обработка сигнала завершения программы
def signal_handler(sig, frame):
    logging.info("Получен сигнал завершения, закрытие устройства.")
    sys.exit(0)

stop_event = threading.Event()

def modbus_task(device):
    while not stop_event.is_set():
        try:    
            data = device.read_data()
            if data:
                logging.info(f"Текущие данные Modbus: {data}")
        except Exception as e:
            stop_event.set()
        time.sleep(5)

def usb_task(device):
    while True:
        device.usb_read_settings()
        device.usb_write_settings("Новые настройки")
        time.sleep(5)

def main():
    signal.signal(signal.SIGINT, signal_handler)

    config = load_config('/workspaces/Proj/config.json')

    global device

    try:
        with BZK03(port_rs485=config['rs485_port'], port_usb=config['usb_port'], baudrate=config['baudrate']) as device:
            modbus_thread = threading.Thread(target=modbus_task, args=(device,))
            usb_thread = threading.Thread(target=usb_task, args=(device,))

            modbus_thread.start()
            usb_thread.start()

            app.run(host='0.0.0.0', port=5000)

            modbus_thread.join()
            usb_thread.join()

    except RuntimeError as e:
        logging.error(f"Ошибка работы устройства: {e}")
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    main()
