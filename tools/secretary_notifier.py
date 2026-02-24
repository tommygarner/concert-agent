import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

load_dotenv()

class SecretaryNotifier:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.email_user = os.getenv("EMAIL_USER")
        self.email_password = os.getenv("EMAIL_PASSWORD")
        self.recipient_email = os.getenv("RECIPIENT_EMAIL")

    def send_notification(self, subject, body):
        if not all([self.email_user, self.email_password, self.recipient_email]):
            print("Email credentials or recipient not configured.")
            return False

        msg = MIMEMultipart()
        msg['From'] = self.email_user
        msg['To'] = self.recipient_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'html'))

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_user, self.email_password)
            text = msg.as_string()
            server.sendmail(self.email_user, self.recipient_email, text)
            server.quit()
            print("Email sent successfully.")
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False

if __name__ == "__main__":
    # Test
    # notifier = SecretaryNotifier()
    # notifier.send_notification("Test Concert Alert", "<h1>Test</h1><p>This is a test.</p>")
    pass
