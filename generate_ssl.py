#!/usr/bin/env python3
"""
Скрипт для генерации самоподписанного SSL сертификата
для работы Telegram WebApp через IP адрес
"""

import os
import subprocess
import sys

def generate_ssl_certificate(ip_address=None):
    """Генерирует самоподписанный SSL сертификат"""
    
    # Создаем папку для сертификатов
    ssl_dir = "ssl"
    os.makedirs(ssl_dir, exist_ok=True)
    
    key_file = os.path.join(ssl_dir, "key.pem")
    cert_file = os.path.join(ssl_dir, "cert.pem")
    
    # Если IP не указан, пытаемся получить из .env
    if not ip_address:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            webapp_url = os.getenv("WEBAPP_URL", "")
            if webapp_url:
                # Извлекаем IP из URL (например, https://90.156.211.211 -> 90.156.211.211)
                ip_address = webapp_url.replace("https://", "").replace("http://", "").split(":")[0].split("/")[0]
        except:
            pass
    
    if not ip_address:
        print("❌ IP адрес не указан!")
        print("Использование: python generate_ssl.py <IP_адрес>")
        print("Или установите WEBAPP_URL в .env файле")
        sys.exit(1)
    
    print(f"🔐 Генерирую SSL сертификат для IP: {ip_address}")
    
    # Команда для генерации сертификата
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:4096",
        "-keyout", key_file,
        "-out", cert_file,
        "-days", "365",
        "-nodes",
        "-subj", f"/C=RU/ST=State/L=City/O=Organization/CN={ip_address}",
        "-addext", f"subjectAltName=IP:{ip_address}"
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ SSL сертификат успешно создан!")
        print(f"   Ключ: {key_file}")
        print(f"   Сертификат: {cert_file}")
        print(f"\n📝 Добавьте в .env файл:")
        print(f"   WEBAPP_URL=https://{ip_address}")
        print(f"\n⚠️  ВАЖНО: Это самоподписанный сертификат.")
        print(f"   Telegram может показать предупреждение о безопасности.")
        print(f"   Это нормально для самоподписанных сертификатов.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка при генерации сертификата: {e}")
        print(f"\n💡 Убедитесь, что OpenSSL установлен:")
        print(f"   Ubuntu/Debian: sudo apt install openssl")
        print(f"   CentOS/RHEL: sudo yum install openssl")
        sys.exit(1)
    except FileNotFoundError:
        print(f"❌ OpenSSL не найден!")
        print(f"\n💡 Установите OpenSSL:")
        print(f"   Ubuntu/Debian: sudo apt install openssl")
        print(f"   CentOS/RHEL: sudo yum install openssl")
        sys.exit(1)

if __name__ == "__main__":
    ip_address = sys.argv[1] if len(sys.argv) > 1 else None
    generate_ssl_certificate(ip_address)

