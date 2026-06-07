import streamlit as st
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import paramiko
from paramiko import SSHClient
import csv
import io
import re
from PIL import Image
import os

# ============================================================
# CONFIGURACIÓN DESDE SECRETS
# ============================================================
# Limpiar espacios en la contraseña de Gmail si existen
gmail_password = st.secrets["email_password"].replace(" ", "")

EMAIL_CONFIG = {
    "smtp_server": st.secrets["smtp_server"],
    "smtp_port": int(st.secrets["smtp_port"]),
    "remitente": st.secrets["email_user"],
    "password": gmail_password,
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
class SSHConnectionManager:
    """Administrador de conexión SSH para mantener la conexión activa"""
    def __init__(self):
        self.ssh = None
        
    def get_connection(self):
        """Obtiene o crea una conexión SSH"""
        if self.ssh is None:
            try:
                self.ssh = SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(
                    hostname=REMOTE_CONFIG["host"],
                    port=REMOTE_CONFIG["port"],
                    username=REMOTE_CONFIG["user"],
                    password=REMOTE_CONFIG["password"],
                    timeout=30
                )
                return self.ssh
            except Exception as e:
                st.error(f"❌ Error de conexión remota: {str(e)}")
                return None
        return self.ssh
    
    def get_sftp(self):
        """Obtiene una conexión SFTP desde la conexión SSH activa"""
        ssh = self.get_connection()
        if ssh:
            try:
                return ssh.open_sftp()
            except Exception as e:
                st.error(f"❌ Error al abrir SFTP: {str(e)}")
                return None
        return None
    
    def close(self):
        """Cierra la conexión SSH"""
        if self.ssh:
            self.ssh.close()
            self.ssh = None

# Crear instancia global del manejador de conexión
ssh_manager = SSHConnectionManager()

def get_sftp():
    """Obtiene una conexión SFTP"""
    return ssh_manager.get_sftp()

# ============================================================
# FUNCIONES PARA LEER/ESCRIBIR CSV REMOTOS
# ============================================================
def leer_csv_remoto(nombre_archivo):
    """Lee un CSV desde el servidor remoto y retorna lista de diccionarios"""
    try:
        sftp = get_sftp()
        if not sftp:
            return []
        
        remote_path = f"{REMOTE_CONFIG['remote_dir']}/{nombre_archivo}"
        
        try:
            with sftp.open(remote_path, 'r') as f:
                content = f.read().decode('utf-8')
            
            if not content:
                return []
            
            reader = csv.DictReader(io.StringIO(content))
            return list(reader)
        
        except FileNotFoundError:
            return []
        except Exception as e:
            st.error(f"Error al leer {nombre_archivo}: {str(e)}")
            return []
    except Exception as e:
        st.error(f"Error general en leer_csv_remoto: {str(e)}")
        return []

def escribir_csv_remoto(nombre_archivo, datos, campos):
    """Escribe un CSV en el servidor remoto"""
    try:
        sftp = get_sftp()
        if not sftp:
            return False
        
        remote_path = f"{REMOTE_CONFIG['remote_dir']}/{nombre_archivo}"
        
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=campos)
        writer.writeheader()
        writer.writerows(datos)
        
        with sftp.open(remote_path, 'w') as f:
            f.write(output.getvalue())
        
        return True
    
    except Exception as e:
        st.error(f"Error al escribir {nombre_archivo}: {str(e)}")
        return False

def agregar_fila_csv_remoto(nombre_archivo, nueva_fila, campos):
    """Agrega una fila a un CSV remoto"""
    datos = leer_csv_remoto(nombre_archivo)
    datos.append(nueva_fila)
    return escribir_csv_remoto(nombre_archivo, datos, campos)

# ============================================================
# FUNCIONES DE NEGOCIO
# ============================================================
def cargar_libros():
    """Carga los libros directamente desde el CSV remoto"""
    libros_raw = leer_csv_remoto(CSV_LIBROS_FILE)
    
    libros = []
    for row in libros_raw:
        if row.get('id') and row['id'].strip().isdigit():
            titulo = row.get('titulo', '').strip()
            if titulo and len(titulo) > 2:
                try:
                    libro = {
                        'id': int(row['id']),
                        'titulo': titulo,
                        'autor': row.get('autor', 'Autor desconocido').strip(),
                        'año': int(row['año']) if row.get('año') and row['año'].strip().isdigit() else 2026,
                        'archivo_pdf': row.get('archivo_pdf', '').strip(),
                        'abstract': row.get('abstract', 'Sin descripción disponible.').strip()
                    }
                    libros.append(libro)
                except ValueError:
                    continue
    
    libros.sort(key=lambda x: x['id'])
    return libros

def registrar_descarga(id_libro, email, exito=True):
    """Registra una descarga en el CSV de lecturas remoto"""
    nueva_descarga = {
        'id_libro': str(id_libro),
        'email': email,
        'fecha': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'ip': 'web',
        'exito': str(exito)
    }
    
    campos = ['id_libro', 'email', 'fecha', 'ip', 'exito']
    return agregar_fila_csv_remoto(CSV_LECTURAS_FILE, nueva_descarga, campos)

def verificar_descarga_previa(id_libro, email):
    """Verifica si un usuario ya ha descargado un libro antes"""
    descargas = leer_csv_remoto(CSV_LECTURAS_FILE)
    
    for descarga in descargas:
        try:
            if (descarga.get('id_libro') and 
                descarga.get('email') and 
                descarga.get('exito') == 'True'):
                if int(descarga['id_libro']) == id_libro and descarga['email'] == email:
                    return True
        except (ValueError, TypeError):
            continue
    return False

def obtener_estadisticas_libro(id_libro):
    """Obtiene estadísticas de descargas exitosas para un libro"""
    descargas = leer_csv_remoto(CSV_LECTURAS_FILE)
    
    count = 0
    for descarga in descargas:
        try:
            if (descarga.get('id_libro') and 
                descarga.get('exito') == 'True'):
                if int(descarga['id_libro']) == id_libro:
                    count += 1
        except (ValueError, TypeError):
            continue
    return count

def obtener_todos_registros():
    """Obtiene todos los registros de descargas"""
    return leer_csv_remoto(CSV_LECTURAS_FILE)

def descargar_pdf_remoto(archivo_pdf):
    """Descarga un PDF directamente del servidor remoto"""
    try:
        sftp = get_sftp()
        if not sftp:
            return None
        
        remote_path = f"{REMOTE_CONFIG['remote_dir']}/{archivo_pdf}"
        
        with sftp.open(remote_path, 'rb') as f:
            pdf_bytes = f.read()
        
        return pdf_bytes
    except Exception as e:
        st.error(f"Error al descargar PDF: {str(e)}")
        return None

def listar_pdfs_remotos():
    """Lista todos los archivos PDF en el directorio remoto"""
    try:
        sftp = get_sftp()
        if not sftp:
            return []
        
        files = sftp.listdir(REMOTE_CONFIG["remote_dir"])
        return [f for f in files if f.lower().endswith('.pdf')]
    except Exception as e:
        st.error(f"Error al listar PDFs: {str(e)}")
        return []

# ============================================================
# FUNCIONES DE EMAIL
# ============================================================
def enviar_pdf_por_email(email_destino, id_libro, archivo_pdf, titulo_libro):
    """Envía el PDF por email al usuario"""
    try:
        if verificar_descarga_previa(id_libro, email_destino):
            return False, "Ya has descargado este libro anteriormente. Revisa tu correo."
        
        if not email_destino or "@" not in email_destino or "." not in email_destino:
            return False, "Por favor, ingresa un correo electrónico válido."
        
        pdf_bytes = descargar_pdf_remoto(archivo_pdf)
        if not pdf_bytes:
            return False, "No se pudo descargar el PDF del servidor remoto."
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG["remitente"]
        msg['To'] = email_destino
        msg['Subject'] = f"📚 Tu libro: {titulo_libro}"
        
        cuerpo = f"""Estimado lector,

Gracias por tu interés en "{titulo_libro}".

Adjunto encontrarás el archivo PDF para que disfrutes de la lectura.

Saludos,
Carlos Polanco

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
        
        return True, "✅ PDF enviado a tu correo. ¡Disfruta la lectura!"
    
    except smtplib.SMTPAuthenticationError:
        registrar_descarga(id_libro, email_destino, exito=False)
        return False, "Error de autenticación con Gmail. Contacta al administrador."
    except smtplib.SMTPRecipientsRefused:
        registrar_descarga(id_libro, email_destino, exito=False)
        return False, f"❌ El correo '{email_destino}' no es válido."
    except Exception as e:
        registrar_descarga(id_libro, email_destino, exito=False)
        return False, f"Error: {str(e)}"

# ============================================================
# FUNCIONES DE ADMINISTRACIÓN
# ============================================================
def sincronizar_pdfs_a_csv():
    """Sincroniza los PDFs del directorio con el CSV de libros"""
    pdfs = listar_pdfs_remotos()
    
    if not pdfs:
        return False, "No se encontraron archivos PDF en el servidor remoto."
    
    libros_existentes = cargar_libros()
    archivos_existentes = [libro['archivo_pdf'] for libro in libros_existentes]
    
    libros_data = leer_csv_remoto(CSV_LIBROS_FILE)
    
    nuevos = 0
    for pdf_file in pdfs:
        if pdf_file not in archivos_existentes:
            max_id = 0
            for libro in libros_data:
                try:
                    if libro.get('id') and libro['id'].isdigit():
                        max_id = max(max_id, int(libro['id']))
                except:
                    pass
            
            nuevo_id = max_id + 1
            
            nombre_sin_pdf = pdf_file.replace('.pdf', '')
            titulo = nombre_sin_pdf.replace('_', ' ').replace('.', ' ')
            
            nuevo_libro = {
                'id': str(nuevo_id),
                'titulo': titulo,
                'autor': 'Por determinar',
                'año': str(datetime.now().year),
                'archivo_pdf': pdf_file,
                'abstract': f'Descripción no disponible para "{titulo}".'
            }
            libros_data.append(nuevo_libro)
            nuevos += 1
    
    if nuevos > 0:
        campos = ['id', 'titulo', 'autor', 'año', 'archivo_pdf', 'abstract']
        if escribir_csv_remoto(CSV_LIBROS_FILE, libros_data, campos):
            return True, f"Sincronización completada. {nuevos} libros nuevos agregados."
        else:
            return False, "Error al guardar los cambios en el servidor."
    else:
        return True, "No se encontraron nuevos libros para agregar."

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
# ESTILOS CSS - ESTILO SIMAK (minimalista, tonos tierra)
# ============================================================
st.markdown("""
<style>
    /* Fondo general */
    .stApp {
        background-color: #f7f4eb;
    }
    
    /* Tipografía */
    h1, h2, h3, .stMarkdown {
        font-family: 'Cormorant Garamond', 'Times New Roman', serif;
        color: #3a3226;
    }
    
    /* Títulos */
    h1 {
        font-size: 2.5rem;
        font-weight: 400;
        letter-spacing: 2px;
        border-bottom: 1px solid #d4cdbc;
        padding-bottom: 0.5rem;
    }
    
    h2 {
        font-size: 1.5rem;
        font-weight: 400;
        color: #5a4e3c;
    }
    
    h3 {
        font-size: 1.2rem;
        font-weight: 400;
        color: #7a6b55;
    }
    
    /* Instrucción en negro intenso */
    .instruction {
        font-size: 0.7rem;
        color: #1a1a1a;
        margin-bottom: 1rem;
        font-family: monospace;
        letter-spacing: 0.5px;
        font-weight: 500;
    }
    
    /* Metadatos */
    .book-meta {
        font-size: 0.75rem;
        color: #9b8e7a;
        font-family: monospace;
    }
    
    /* Abstract */
    .abstract {
        background-color: #faf8f3;
        padding: 1rem 1.2rem;
        margin: 0.5rem 0 1rem 0;
        border-left: 3px solid #c4bbaa;
        font-size: 0.85rem;
        line-height: 1.4;
        color: #5a4e3c;
        font-style: italic;
    }
    
    /* Sidebar minimalista */
    [data-testid="stSidebar"] {
        background-color: #faf8f3;
        border-right: 1px solid #e8e2d6;
    }
    
    [data-testid="stSidebar"] .stRadio label {
        font-family: monospace;
        color: #7a6b55;
        font-size: 0.8rem;
    }
    
    /* Footer */
    .footer {
        font-size: 0.65rem;
        text-align: center;
        margin-top: 3rem;
        padding-top: 1rem;
        border-top: 1px solid #e8e2d6;
        color: #b0a692;
    }
    
    .footer a {
        color: #8b7a62;
        text-decoration: none;
    }
    
    .footer a:hover {
        text-decoration: underline;
    }
    
    /* Mensajes - ancho completo y mejor visualización */
    .stAlert {
        width: 100% !important;
        margin: 0.5rem 0 !important;
        padding: 0.75rem 1rem !important;
        border-radius: 4px !important;
        font-family: monospace !important;
        font-size: 0.85rem !important;
        line-height: 1.4 !important;
    }
    
    /* Asegurar que todos los mensajes ocupen todo el ancho */
    div[data-testid="stAlert"] {
        width: 100% !important;
        min-width: 100% !important;
    }
    
    /* Mensaje de éxito */
    .stAlert[data-baseweb="alert"] {
        background-color: #f0f4ec !important;
        border-left: 3px solid #4caf50 !important;
    }
    
    /* Mensaje de error */
    .stAlert[data-baseweb="alert"][kind="error"] {
        border-left: 3px solid #f44336 !important;
    }
    
    /* Mensaje de advertencia */
    .stAlert[data-baseweb="alert"][kind="warning"] {
        border-left: 3px solid #ff9800 !important;
    }
    
    /* Mensaje de información */
    .stAlert[data-baseweb="alert"][kind="info"] {
        border-left: 3px solid #2196f3 !important;
    }
    
    /* Contenedor de mensajes global */
    .stMarkdown .stAlert, 
    div:has(> .stAlert) {
        width: 100% !important;
    }
    
    /* Spinner */
    .stSpinner > div {
        border-color: #c4bbaa !important;
    }
    
    /* Info box */
    .stInfo {
        background-color: #faf8f3;
        font-family: monospace;
        width: 100%;
    }
    
    /* Botones de Streamlit personalizados */
    div[data-testid="column"] button {
        background: transparent;
        border: none;
        color: #5a4e3c;
        font-family: monospace;
        font-size: 0.8rem;
        text-align: left;
        padding: 0.2rem 0;
        transition: color 0.2s ease;
    }
    
    div[data-testid="column"] button:hover {
        color: #8b7a62;
        background: transparent;
        border: none;
    }
    
    /* Botón Enviar específico */
    button[key^="send_"] {
        background: transparent;
        border: 1px solid #d4cdbc;
        border-radius: 20px;
        padding: 0.2rem 0.8rem;
        font-size: 0.7rem;
        text-align: center;
    }
    
    button[key^="send_"]:hover {
        background-color: #f0ece4;
        border-color: #a69b84;
    }
    
    /* Botón de título */
    button[key^="btn_"] {
        font-weight: 500;
        font-size: 0.95rem;
        padding: 0;
        margin: 0;
    }
    
    /* Botón sincronizar */
    button[key="sync_btn"] {
        background: transparent;
        border: 1px solid #d4cdbc;
        border-radius: 20px;
        padding: 0.3rem 1rem;
        font-size: 0.75rem;
        color: #7a6b55;
    }
    
    button[key="sync_btn"]:hover {
        background-color: #f0ece4;
        border-color: #a69b84;
        color: #5a4e3c;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# INICIALIZACIÓN
# ============================================================
def inicializar_csvs_remotos():
    """Inicializa los archivos CSV en el servidor remoto si no existen"""
    try:
        sftp = get_sftp()
        if not sftp:
            return
        
        libros_path = f"{REMOTE_CONFIG['remote_dir']}/{CSV_LIBROS_FILE}"
        lecturas_path = f"{REMOTE_CONFIG['remote_dir']}/{CSV_LECTURAS_FILE}"
        
        try:
            sftp.stat(libros_path)
        except FileNotFoundError:
            with sftp.open(libros_path, 'w') as f:
                f.write('id,titulo,autor,año,archivo_pdf,abstract\n')
        
        try:
            sftp.stat(lecturas_path)
        except FileNotFoundError:
            with sftp.open(lecturas_path, 'w') as f:
                f.write('id_libro,email,fecha,ip,exito\n')
    
    except Exception as e:
        st.error(f"Error al inicializar archivos remotos: {e}")

# Intentar inicializar los CSVs remotos
try:
    inicializar_csvs_remotos()
except Exception as e:
    st.error(f"Error de inicialización: {e}")

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### Navegación")
    opcion = st.radio(
        "Ir a:", 
        ["Biblioteca", "Sobre el autor", "Admin"], 
        label_visibility="collapsed"
    )

# ============================================================
# HEADER
# ============================================================
st.markdown("# Biblioteca de Ciencia Ficción")
#st.markdown("### *Relatos de Ciencia ficció*")

# ============================================================
# BIBLIOTECA
# ============================================================
if opcion == "Biblioteca":
    st.markdown("## Libros disponibles")
    
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("⟳ Sincronizar", key="sync_btn"):
            with st.spinner("Sincronizando..."):
                success, msg = sincronizar_pdfs_a_csv()
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    
    libros = cargar_libros()
    
    if not libros:
        st.info("📭 No hay libros disponibles. Usa 'Sincronizar' para cargar los libros.")
    else:
        # Instrucción en negro intenso
        st.markdown('<p class="instruction">⌵ Haz clic en el título para leer la sinopsis</p>', unsafe_allow_html=True)
        
        for libro in libros:
            key_abstract = f"show_abstract_{libro['id']}"
            if key_abstract not in st.session_state:
                st.session_state[key_abstract] = False
            
            # Crear un contenedor para la fila del libro
            with st.container():
                col_titulo, col_autor, col_stats, col_email, col_boton = st.columns([2.5, 1.2, 0.8, 1.5, 0.8])
                
                with col_titulo:
                    if st.button(
                        libro['titulo'], 
                        key=f"btn_{libro['id']}", 
                        use_container_width=True
                    ):
                        st.session_state[key_abstract] = not st.session_state[key_abstract]
                
                with col_autor:
                    st.markdown(f'<span class="book-meta">{libro["autor"]}</span>', unsafe_allow_html=True)
                
                with col_stats:
                    num_descargas = obtener_estadisticas_libro(libro['id'])
                    st.markdown(f'<span class="book-meta">📥 {num_descargas}</span>', unsafe_allow_html=True)
                
                with col_email:
                    email_input = st.text_input(
                        "", 
                        key=f"email_{libro['id']}", 
                        placeholder="correo",
                        label_visibility="collapsed"
                    )
                
                with col_boton:
                    if st.button("Enviar", key=f"send_{libro['id']}", use_container_width=True):
                        # Validar email y procesar envío
                        if email_input and "@" in email_input and "." in email_input:
                            if verificar_descarga_previa(libro['id'], email_input):
                                # Usar placeholder fuera de las columnas
                                st.session_state[f'warning_{libro["id"]}'] = "⚠️ Ya descargaste este libro anteriormente"
                                st.session_state[f'error_{libro["id"]}'] = None
                                st.session_state[f'success_{libro["id"]}'] = None
                            else:
                                with st.spinner("📨 Enviando..."):
                                    success, msg = enviar_pdf_por_email(
                                        email_input, 
                                        libro['id'], 
                                        libro['archivo_pdf'], 
                                        libro['titulo']
                                    )
                                    if success:
                                        st.session_state[f'success_{libro["id"]}'] = msg
                                        st.session_state[f'error_{libro["id"]}'] = None
                                        st.session_state[f'warning_{libro["id"]}'] = None
                                    else:
                                        st.session_state[f'error_{libro["id"]}'] = msg
                                        st.session_state[f'success_{libro["id"]}'] = None
                                        st.session_state[f'warning_{libro["id"]}'] = None
                        else:
                            st.session_state[f'warning_{libro["id"]}'] = "📧 Por favor, ingresa un correo electrónico válido"
                            st.session_state[f'error_{libro["id"]}'] = None
                            st.session_state[f'success_{libro["id"]}'] = None
                
                # Mostrar mensajes fuera de las columnas (a ancho completo)
                if st.session_state.get(f'success_{libro["id"]}'):
                    st.success(st.session_state[f'success_{libro["id"]}'])
                if st.session_state.get(f'error_{libro["id"]}'):
                    st.error(st.session_state[f'error_{libro["id"]}'])
                if st.session_state.get(f'warning_{libro["id"]}'):
                    st.warning(st.session_state[f'warning_{libro["id"]}'])
                
                # Abstract debajo de la fila
                if st.session_state[key_abstract]:
                    st.markdown(f'<div class="abstract">📖 {libro["abstract"]}</div>', unsafe_allow_html=True)

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
            st.markdown("### ✍️ Carlos Polanco")
            st.markdown("""
            **Escritor de ciencia ficción**
            
            Escribo ciencia ficción porque creo que los mejores futuros posibles 
            empiezan con preguntas incómodas en el presente.
            
            Mis relatos exploran la memoria, el tiempo y la conciencia humana.
            """)
    else:
        st.markdown("### ✍️ Carlos Polanco")
        st.markdown("""
        **Escritor de ciencia ficción**
        
        Escribo ciencia ficción porque creo que los mejores futuros posibles 
        empiezan con preguntas incómodas en el presente.
        
        Mis relatos exploran la memoria, el tiempo y la conciencia humana.
        """)
    
    st.markdown("---")
    st.markdown("**📧 Contacto:** [polanco@unam.mx](mailto:polanco@unam.mx)")

# ============================================================
# ADMIN
# ============================================================
elif opcion == "Admin":
    st.markdown("## Panel de administración")
    
    tab1, tab2 = st.tabs(["📚 Catálogo", "📊 Descargas"])
    
    with tab1:
        libros = cargar_libros()
        st.write(f"**Total de libros:** {len(libros)}")
        st.markdown("---")
        if libros:
            for libro in libros:
                with st.expander(f"📖 ID {libro['id']}: {libro['titulo']}"):
                    st.markdown(f"**Autor:** {libro['autor']}")
                    st.markdown(f"**Año:** {libro['año']}")
                    st.markdown(f"**Archivo PDF:** `{libro['archivo_pdf']}`")
                    st.markdown(f"**Descargas:** {obtener_estadisticas_libro(libro['id'])}")
                    st.markdown("**Sinopsis:**")
                    st.info(libro['abstract'][:500] + "..." if len(libro['abstract']) > 500 else libro['abstract'])
        else:
            st.info("📭 No hay libros en el catálogo.")
    
    with tab2:
        registros = obtener_todos_registros()
        st.write(f"**Total de descargas:** {len(registros)}")
        st.markdown("---")
        if registros:
            for reg in reversed(registros[-50:]):
                estado = "✅" if reg.get('exito') == 'True' else "❌"
                st.markdown(f"- {estado} **{reg['fecha']}** | 📧 {reg['email']} | 📚 Libro ID: {reg['id_libro']}")
        else:
            st.info("📭 No hay registros de descargas aún.")

# ============================================================
# FOOTER
# ============================================================
st.markdown('<div class="footer">© 2026 · Biblioteca de Ciencia Ficción · <a href="mailto:polanco@unam.mx">Contacto</a></div>', unsafe_allow_html=True)
