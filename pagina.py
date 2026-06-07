import streamlit as st
from datetime import datetime
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import paramiko
from paramiko import SSHClient
import csv
import os
import tempfile
import re
from PIL import Image
import webbrowser
import threading
import time

# ============================================================
# ABRIR NAVEGADOR AUTOMÁTICAMENTE (solo en ejecución local)
# ============================================================
def abrir_navegador():
    """Abre el navegador automáticamente después de un breve retraso"""
    time.sleep(1.5)
    webbrowser.open('http://localhost:8501')

# Verificar si está en ejecución local (no en Streamlit Cloud)
if not os.environ.get('STREAMLIT_CLOUD', False):
    threading.Thread(target=abrir_navegador, daemon=True).start()

# ============================================================
# CONFIGURACIÓN DESDE SECRETS
# ============================================================
EMAIL_CONFIG = {
    "smtp_server": st.secrets["smtp_server"],
    "smtp_port": int(st.secrets["smtp_port"]),
    "remitente": st.secrets["email_user"],
    "password": st.secrets["email_password"],
    "notificacion": st.secrets["notification_email"]
}

REMOTE_CONFIG = {
    "host": st.secrets["remote_host"],
    "user": st.secrets["remote_user"],
    "password": st.secrets["remote_password"],
    "port": int(st.secrets["remote_port"]),
    "remote_dir": st.secrets["remote_dir"]
}

CSV_LIBROS_FILE = st.secrets["csv_libros_file"]
CSV_LECTURAS_FILE = st.secrets["csv_lecturas_file"]

# ============================================================
# FUNCIONES DE CONEXIÓN REMOTA
# ============================================================
def connect_sftp():
    """Establece conexión SFTP con el servidor remoto"""
    try:
        ssh = SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=REMOTE_CONFIG["host"],
            port=REMOTE_CONFIG["port"],
            username=REMOTE_CONFIG["user"],
            password=REMOTE_CONFIG["password"],
            timeout=10
        )
        return ssh
    except Exception as e:
        st.error(f"Error de conexión remota: {str(e)}")
        return None

# ============================================================
# INICIALIZACIÓN DE CSVs (LOCAL Y REMOTO)
# ============================================================
def inicializar_csvs():
    """Inicializa los archivos CSV localmente y en el servidor remoto si no existen"""
    
    # === INICIALIZACIÓN LOCAL ===
    if not os.path.exists(CSV_LIBROS_FILE):
        with open(CSV_LIBROS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'titulo', 'autor', 'año', 'sinopsis', 'archivo_pdf', 'abstract'])
        print(f"✅ Archivo local creado: {CSV_LIBROS_FILE}")
    
    if not os.path.exists(CSV_LECTURAS_FILE):
        with open(CSV_LECTURAS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id_libro', 'email', 'fecha', 'ip', 'exito'])
        print(f"✅ Archivo local creado: {CSV_LECTURAS_FILE}")
    
    # === INICIALIZACIÓN EN SERVIDOR REMOTO ===
    try:
        ssh = connect_sftp()
        if ssh:
            sftp = ssh.open_sftp()
            
            # Verificar/Crear directorio remoto
            try:
                sftp.stat(REMOTE_CONFIG["remote_dir"])
                print(f"✅ Directorio remoto existe: {REMOTE_CONFIG['remote_dir']}")
            except FileNotFoundError:
                try:
                    # Crear directorio y subdirectorios necesarios
                    current_path = ""
                    for part in REMOTE_CONFIG["remote_dir"].strip('/').split('/'):
                        current_path += "/" + part
                        try:
                            sftp.stat(current_path)
                        except FileNotFoundError:
                            sftp.mkdir(current_path)
                            print(f"📁 Directorio creado: {current_path}")
                except Exception as e:
                    print(f"⚠️ No se pudo crear directorio remoto: {e}")
            
            # Archivos CSV remotos
            remote_csv_libros = os.path.join(REMOTE_CONFIG["remote_dir"], CSV_LIBROS_FILE)
            remote_csv_lecturas = os.path.join(REMOTE_CONFIG["remote_dir"], CSV_LECTURAS_FILE)
            
            # Crear CSV de libros remoto
            try:
                sftp.stat(remote_csv_libros)
            except FileNotFoundError:
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', delete=False) as tmp:
                    writer = csv.writer(tmp)
                    writer.writerow(['id', 'titulo', 'autor', 'año', 'sinopsis', 'archivo_pdf', 'abstract'])
                    tmp_path = tmp.name
                sftp.put(tmp_path, remote_csv_libros)
                os.unlink(tmp_path)
                print(f"📄 Archivo remoto creado: {remote_csv_libros}")
            
            # Crear CSV de lecturas remoto
            try:
                sftp.stat(remote_csv_lecturas)
            except FileNotFoundError:
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', delete=False) as tmp:
                    writer = csv.writer(tmp)
                    writer.writerow(['id_libro', 'email', 'fecha', 'ip', 'exito'])
                    tmp_path = tmp.name
                sftp.put(tmp_path, remote_csv_lecturas)
                os.unlink(tmp_path)
                print(f"📄 Archivo remoto creado: {remote_csv_lecturas}")
            
            sftp.close()
            ssh.close()
            print("✅ Inicialización remota completada")
    except Exception as e:
        print(f"⚠️ Error al inicializar archivos remotos: {e}")

# ============================================================
# FUNCIONES DE MANEJO DE CSV
# ============================================================
def cargar_libros_desde_csv():
    """Carga los libros desde el CSV local"""
    libros = []
    try:
        if os.path.exists(CSV_LIBROS_FILE):
            with open(CSV_LIBROS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row['id'] = int(row['id'])
                    row['año'] = int(row['año'])
                    libros.append(row)
    except Exception as e:
        st.error(f"Error al cargar libros: {str(e)}")
    return libros

def guardar_libro_en_csv(libro_data):
    """Guarda un nuevo libro en el CSV"""
    try:
        libros = cargar_libros_desde_csv()
        
        if len(libros) == 0:
            nuevo_id = 1
        else:
            nuevo_id = max(libro['id'] for libro in libros) + 1
        
        libro_data['id'] = nuevo_id
        
        with open(CSV_LIBROS_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                libro_data['id'],
                libro_data['titulo'],
                libro_data['autor'],
                libro_data['año'],
                libro_data['sinopsis'],
                libro_data['archivo_pdf'],
                libro_data['abstract']
            ])
        
        return nuevo_id
    except Exception as e:
        st.error(f"Error al guardar libro: {str(e)}")
        return None

def registrar_descarga(id_libro, email, exito=True):
    """Registra una descarga en el CSV de lecturas"""
    try:
        with open(CSV_LECTURAS_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                id_libro,
                email,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'local',
                exito
            ])
        
        enviar_notificacion_autor(id_libro, email, exito)
        return True
    except Exception as e:
        print(f"Error al registrar descarga: {str(e)}")
        return False

def verificar_descarga_previa(id_libro, email):
    """Verifica si un usuario ya ha descargado un libro antes"""
    try:
        if os.path.exists(CSV_LECTURAS_FILE):
            with open(CSV_LECTURAS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if int(row['id_libro']) == id_libro and row['email'] == email and row['exito'] == 'True':
                        return True
        return False
    except:
        return False

def obtener_estadisticas_libro(id_libro):
    """Obtiene estadísticas de descargas para un libro"""
    try:
        count = 0
        if os.path.exists(CSV_LECTURAS_FILE):
            with open(CSV_LECTURAS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if int(row['id_libro']) == id_libro and row['exito'] == 'True':
                        count += 1
        return count
    except:
        return 0

def obtener_todos_registros():
    """Obtiene todos los registros de descargas"""
    registros = []
    try:
        if os.path.exists(CSV_LECTURAS_FILE):
            with open(CSV_LECTURAS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    registros.append(row)
    except:
        pass
    return registros

# ============================================================
# FUNCIONES DE SINCRONIZACIÓN Y DESCARGA
# ============================================================
def sincronizar_libros_remotos():
    """Sincroniza los libros del servidor remoto con el CSV local"""
    ssh = connect_sftp()
    if not ssh:
        return False, "No se pudo conectar al servidor remoto"
    
    try:
        sftp = ssh.open_sftp()
        
        try:
            files = sftp.listdir(REMOTE_CONFIG["remote_dir"])
            pdfs_remotos = [f for f in files if f.lower().endswith('.pdf')]
        except FileNotFoundError:
            return False, f"Directorio remoto no encontrado: {REMOTE_CONFIG['remote_dir']}"
        
        libros_existentes = cargar_libros_desde_csv()
        archivos_existentes = [libro['archivo_pdf'] for libro in libros_existentes]
        
        nuevos_libros = 0
        for pdf_file in pdfs_remotos:
            if pdf_file in archivos_existentes:
                continue
            
            nombre_sin_pdf = pdf_file.replace('.pdf', '')
            match = re.match(r'^(\d+)_(.+)$', nombre_sin_pdf)
            if match:
                titulo = match.group(2).replace('_', ' ')
            else:
                titulo = nombre_sin_pdf.replace('_', ' ')
            
            nuevo_libro = {
                'id': None,
                'titulo': titulo,
                'autor': 'Por determinar',
                'año': datetime.now().year,
                'sinopsis': f'Sinopsis no disponible para "{titulo}".',
                'archivo_pdf': pdf_file,
                'abstract': f'Abstract no disponible. Contenido: {titulo}'
            }
            guardar_libro_en_csv(nuevo_libro)
            nuevos_libros += 1
        
        sftp.close()
        ssh.close()
        
        return True, f"Sincronización completada. {nuevos_libros} libros nuevos agregados."
    
    except Exception as e:
        return False, f"Error en sincronización: {str(e)}"

def descargar_pdf_remoto(archivo_pdf):
    """Descarga un PDF del servidor remoto"""
    ssh = connect_sftp()
    if not ssh:
        return None
    
    try:
        sftp = ssh.open_sftp()
        remote_path = os.path.join(REMOTE_CONFIG["remote_dir"], archivo_pdf)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            sftp.get(remote_path, tmp_file.name)
            with open(tmp_file.name, 'rb') as f:
                pdf_bytes = f.read()
            os.unlink(tmp_file.name)
        
        sftp.close()
        ssh.close()
        return pdf_bytes
    
    except Exception as e:
        st.error(f"Error al descargar PDF: {str(e)}")
        return None

# ============================================================
# FUNCIONES DE EMAIL
# ============================================================
def enviar_notificacion_autor(id_libro, email_usuario, exito):
    """Envía notificación al autor sobre descargas"""
    try:
        libros = cargar_libros_desde_csv()
        titulo_libro = "Desconocido"
        for libro in libros:
            if libro['id'] == id_libro:
                titulo_libro = libro['titulo']
                break
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG["remitente"]
        msg['To'] = EMAIL_CONFIG["notificacion"]
        msg['Subject'] = f"Nueva descarga - {titulo_libro}"
        
        estado = "EXITOSA" if exito else "FALLIDA"
        cuerpo = f"""
        Nueva solicitud de libro registrada:
        
        📚 Libro: {titulo_libro} (ID: {id_libro})
        📧 Solicitante: {email_usuario}
        ✅ Estado: {estado}
        🕐 Fecha: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        """
        
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        
        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["remitente"], EMAIL_CONFIG["password"])
            server.send_message(msg)
        
        return True
    except Exception as e:
        print(f"Error en notificación: {str(e)}")
        return False

def enviar_pdf_por_email(email_destino, id_libro, archivo_pdf, titulo_libro):
    """Envía el PDF por email al usuario"""
    try:
        if verificar_descarga_previa(id_libro, email_destino):
            return False, "Ya has descargado este libro anteriormente. Revisa tu correo."
        
        pdf_bytes = descargar_pdf_remoto(archivo_pdf)
        
        if not pdf_bytes:
            return False, "No se pudo descargar el PDF del servidor remoto."
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG["remitente"]
        msg['To'] = email_destino
        msg['Subject'] = f"Tu libro: {titulo_libro}"
        
        cuerpo = f"""Estimado lector,

Gracias por tu interés en "{titulo_libro}".

Adjunto encontrarás el archivo PDF para que disfrutes de la lectura.

Saludos desde el espacio,

El autor
---
Si no solicitaste este libro, ignora este mensaje."""
        
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        
        pdf_adjunto = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_adjunto.add_header('Content-Disposition', 'attachment', filename=f"{titulo_libro}.pdf")
        msg.attach(pdf_adjunto)
        
        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["remitente"], EMAIL_CONFIG["password"])
            server.send_message(msg)
        
        registrar_descarga(id_libro, email_destino, exito=True)
        
        return True, "PDF enviado a tu correo electrónico. ¡Disfruta la lectura!"
    
    except Exception as e:
        registrar_descarga(id_libro, email_destino, exito=False)
        return False, f"Error al enviar: {str(e)}"

# ============================================================
# FUNCIÓN PARA MOSTRAR FOTO
# ============================================================
def mostrar_foto_autor():
    """Carga y muestra la foto del autor"""
    posibles_rutas = [
        "fotorecortada9.jpg",
        "images/fotorecortada9.jpg",
        "img/fotorecortada9.jpg",
        "assets/fotorecortada9.jpg",
        "fotos/fotorecortada9.jpg"
    ]
    
    for ruta in posibles_rutas:
        if os.path.exists(ruta):
            try:
                return Image.open(ruta)
            except:
                pass
    return None

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Biblioteca de Ciencia Ficción",
    page_icon="📖",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ============================================================
# ESTILOS CSS
# ============================================================
st.markdown("""
<style>
    .stApp { background-color: #f5f0e8; }
    h1, h2, h3 { font-family: 'Cormorant Garamond', serif; color: #2c2418; }
    .book-entry { border-bottom: 1px solid #e0d6c8; padding: 1.5rem 0; }
    .book-title { font-size: 1.5rem; font-weight: 600; cursor: pointer; }
    .abstract { background-color: #faf8f4; padding: 1rem; border-left: 3px solid #c4b8a8; }
    .footer { font-size: 0.7rem; text-align: center; margin-top: 3rem; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# INICIALIZACIÓN
# ============================================================
inicializar_csvs()

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### Navegación")
    opcion = st.radio("Ir a:", ["Biblioteca", "Sobre el autor", "Admin"], label_visibility="collapsed")

# ============================================================
# HEADER
# ============================================================
st.markdown("# Biblioteca de Ciencia Ficción")
st.markdown("### *Relatos del espacio y la consciencia*")

# ============================================================
# BIBLIOTECA
# ============================================================
if opcion == "Biblioteca":
    st.markdown("## Libros disponibles")
    
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Sincronizar con biblioteca remota"):
            with st.spinner("Sincronizando..."):
                success, msg = sincronizar_libros_remotos()
                if success:
                    st.success(msg)
                else:
                    st.error(msg)
    
    libros = cargar_libros_desde_csv()
    
    if not libros:
        st.info("No hay libros disponibles. Usa 'Sincronizar' para cargar los libros desde el servidor remoto.")
    else:
        for libro in libros:
            key_abstract = f"show_abstract_{libro['id']}"
            if key_abstract not in st.session_state:
                st.session_state[key_abstract] = False
            
            col_titulo, col_email = st.columns([2, 1])
            
            with col_titulo:
                if st.button(libro['titulo'], key=f"btn_{libro['id']}"):
                    st.session_state[key_abstract] = not st.session_state[key_abstract]
                st.markdown(f"<small>{libro['autor']} · {libro['año']}</small>", unsafe_allow_html=True)
                num_descargas = obtener_estadisticas_libro(libro['id'])
                st.markdown(f"<small>📊 {num_descargas} descargas</small>", unsafe_allow_html=True)
            
            with col_email:
                email = st.text_input("Correo", key=f"email_{libro['id']}", placeholder="tu@email.com", label_visibility="collapsed")
                if st.button("📧 Recibir PDF", key=f"enviar_{libro['id']}"):
                    if email and "@" in email:
                        if verificar_descarga_previa(libro['id'], email):
                            st.warning("Ya has descargado este libro anteriormente. Revisa tu correo.")
                        else:
                            with st.spinner("Enviando..."):
                                success, msg = enviar_pdf_por_email(email, libro['id'], libro['archivo_pdf'], libro['titulo'])
                                if success:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                    else:
                        st.warning("Por favor, ingresa un correo válido.")
            
            if st.session_state[key_abstract]:
                st.markdown(f"<div class='abstract'>{libro['abstract']}</div>", unsafe_allow_html=True)
            
            st.markdown("<div class='book-entry'></div>", unsafe_allow_html=True)

# ============================================================
# SOBRE EL AUTOR
# ============================================================
elif opcion == "Sobre el autor":
    st.markdown("## Sobre el autor")
    
    foto = mostrar_foto_autor()
    if foto:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(foto, use_container_width=True)
        with col2:
            st.markdown("### Escritor de ciencia ficción\n\nEscribo ciencia ficción porque creo que los mejores futuros posibles empiezan con preguntas incómodas en el presente.")
    else:
        st.markdown("### Escritor de ciencia ficción\n\nEscribo ciencia ficción porque creo que los mejores futuros posibles empiezan con preguntas incómodas en el presente.")
    
    st.markdown("---\n**Contacto:** [polanco@unam.mx](mailto:polanco@unam.mx)")

# ============================================================
# ADMIN
# ============================================================
elif opcion == "Admin":
    st.markdown("## Panel de administración")
    
    tab1, tab2 = st.tabs(["📚 Libros", "📊 Descargas"])
    
    with tab1:
        libros = cargar_libros_desde_csv()
        st.write(f"**Total libros:** {len(libros)}")
        for libro in libros:
            st.markdown(f"- **ID {libro['id']}:** {libro['titulo']} - `{libro['archivo_pdf']}`")
    
    with tab2:
        registros = obtener_todos_registros()
        st.write(f"**Total descargas:** {len(registros)}")
        for reg in registros[-20:]:
            st.markdown(f"- {reg['fecha']} - {reg['email']} - Libro ID: {reg['id_libro']}")

# ============================================================
# FOOTER
# ============================================================
st.markdown('<div class="footer">© 2026 · Biblioteca de Ciencia Ficción</div>', unsafe_allow_html=True)
