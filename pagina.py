import streamlit as st
from datetime import datetime
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import paramiko
from paramiko import SSHClient
import pandas as pd
import os
import tempfile
import re
from PIL import Image

# -----------------------------
# CONFIGURACIÓN DESDE SECRETS
# -----------------------------
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

# -----------------------------
# CONFIGURACIÓN DE PÁGINA
# -----------------------------
st.set_page_config(
    page_title="Biblioteca de Ciencia Ficción",
    page_icon="📖",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# -----------------------------
# FUNCIONES DE MANEJO DE CSV
# -----------------------------
def inicializar_csvs():
    """Inicializa los archivos CSV si no existen"""
    # CSV de libros: id, titulo, autor, año, sinopsis, archivo_pdf, abstract
    if not os.path.exists(CSV_LIBROS_FILE):
        df_libros = pd.DataFrame(columns=[
            'id', 'titulo', 'autor', 'año', 'sinopsis', 'archivo_pdf', 'abstract'
        ])
        df_libros.to_csv(CSV_LIBROS_FILE, index=False)
    
    # CSV de lecturas: id_libro, email, fecha, ip, exito
    if not os.path.exists(CSV_LECTURAS_FILE):
        df_lecturas = pd.DataFrame(columns=[
            'id_libro', 'email', 'fecha', 'ip', 'exito'
        ])
        df_lecturas.to_csv(CSV_LECTURAS_FILE, index=False)

def cargar_libros_desde_csv():
    """Carga los libros desde el CSV local"""
    try:
        if os.path.exists(CSV_LIBROS_FILE):
            df = pd.read_csv(CSV_LIBROS_FILE)
            return df.to_dict('records')
        else:
            return []
    except Exception as e:
        st.error(f"Error al cargar libros: {str(e)}")
        return []

def guardar_libro_en_csv(libro_data):
    """Guarda un nuevo libro en el CSV"""
    try:
        df = pd.read_csv(CSV_LIBROS_FILE) if os.path.exists(CSV_LIBROS_FILE) else pd.DataFrame(columns=[
            'id', 'titulo', 'autor', 'año', 'sinopsis', 'archivo_pdf', 'abstract'
        ])
        
        # Asignar nuevo ID
        if len(df) == 0:
            libro_data['id'] = 1
        else:
            libro_data['id'] = df['id'].max() + 1
        
        df = pd.concat([df, pd.DataFrame([libro_data])], ignore_index=True)
        df.to_csv(CSV_LIBROS_FILE, index=False)
        return libro_data['id']
    except Exception as e:
        st.error(f"Error al guardar libro: {str(e)}")
        return None

def registrar_descarga(id_libro, email, exito=True):
    """Registra una descarga en el CSV de lecturas"""
    try:
        df = pd.read_csv(CSV_LECTURAS_FILE) if os.path.exists(CSV_LECTURAS_FILE) else pd.DataFrame(columns=[
            'id_libro', 'email', 'fecha', 'ip', 'exito'
        ])
        
        nuevo_registro = pd.DataFrame([{
            'id_libro': id_libro,
            'email': email,
            'fecha': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'ip': st.request.client_ip if hasattr(st, 'request') else 'desconocida',
            'exito': exito
        }])
        
        df = pd.concat([df, nuevo_registro], ignore_index=True)
        df.to_csv(CSV_LECTURAS_FILE, index=False)
        
        # Enviar notificación al autor
        enviar_notificacion_autor(id_libro, email, exito)
        
        return True
    except Exception as e:
        print(f"Error al registrar descarga: {str(e)}")
        return False

def verificar_descarga_previa(id_libro, email):
    """Verifica si un usuario ya ha descargado un libro antes"""
    try:
        if os.path.exists(CSV_LECTURAS_FILE):
            df = pd.read_csv(CSV_LECTURAS_FILE)
            descargas = df[(df['id_libro'] == id_libro) & (df['email'] == email) & (df['exito'] == True)]
            return len(descargas) > 0
        return False
    except:
        return False

def obtener_estadisticas_libro(id_libro):
    """Obtiene estadísticas de descargas para un libro"""
    try:
        if os.path.exists(CSV_LECTURAS_FILE):
            df = pd.read_csv(CSV_LECTURAS_FILE)
            descargas = df[(df['id_libro'] == id_libro) & (df['exito'] == True)]
            return len(descargas)
        return 0
    except:
        return 0

# -----------------------------
# FUNCIONES DE CONEXIÓN REMOTA
# -----------------------------
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

def sincronizar_libros_remotos():
    """Sincroniza los libros del servidor remoto con el CSV local"""
    ssh = connect_sftp()
    if not ssh:
        return False, "No se pudo conectar al servidor remoto"
    
    try:
        sftp = ssh.open_sftp()
        
        # Listar archivos PDF en el directorio remoto
        try:
            files = sftp.listdir(REMOTE_CONFIG["remote_dir"])
            pdfs_remotos = [f for f in files if f.lower().endswith('.pdf')]
        except FileNotFoundError:
            return False, f"Directorio remoto no encontrado: {REMOTE_CONFIG['remote_dir']}"
        
        # Cargar libros existentes
        df_libros = pd.read_csv(CSV_LIBROS_FILE) if os.path.exists(CSV_LIBROS_FILE) else pd.DataFrame()
        
        nuevos_libros = 0
        for pdf_file in pdfs_remotos:
            # Buscar si el libro ya está registrado por su archivo_pdf
            if len(df_libros) > 0 and pdf_file in df_libros['archivo_pdf'].values:
                continue
            
            # Intentar extraer metadatos del nombre del archivo
            # Formato esperado: "Titulo Del Libro.pdf" o "1000_Titulo.pdf"
            nombre_sin_pdf = pdf_file.replace('.pdf', '')
            
            # Si tiene formato "id_titulo"
            match = re.match(r'^(\d+)_(.+)$', nombre_sin_pdf)
            if match:
                libro_id = int(match.group(1))
                titulo = match.group(2).replace('_', ' ')
            else:
                libro_id = None
                titulo = nombre_sin_pdf.replace('_', ' ')
            
            # Crear registro del libro
            nuevo_libro = {
                'id': libro_id if libro_id else (df_libros['id'].max() + 1 if len(df_libros) > 0 else 1),
                'titulo': titulo,
                'autor': 'Por determinar',
                'año': datetime.now().year,
                'sinopsis': f'Sinopsis no disponible para "{titulo}". Por favor, contacta al administrador.',
                'archivo_pdf': pdf_file,
                'abstract': f'Abstract no disponible. Contenido: {titulo}'
            }
            
            if len(df_libros) == 0:
                df_libros = pd.DataFrame([nuevo_libro])
            else:
                df_libros = pd.concat([df_libros, pd.DataFrame([nuevo_libro])], ignore_index=True)
            
            nuevos_libros += 1
        
        # Guardar CSV actualizado
        df_libros.to_csv(CSV_LIBROS_FILE, index=False)
        
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
        
        # Leer archivo en memoria
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

# -----------------------------
# FUNCIONES DE EMAIL
# -----------------------------
def enviar_notificacion_autor(id_libro, email_usuario, exito):
    """Envía notificación al autor sobre descargas"""
    try:
        # Obtener información del libro
        df_libros = pd.read_csv(CSV_LIBROS_FILE) if os.path.exists(CSV_LIBROS_FILE) else pd.DataFrame()
        libro = df_libros[df_libros['id'] == id_libro]
        titulo_libro = libro['titulo'].values[0] if len(libro) > 0 else f"ID {id_libro}"
        
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
        # Verificar si ya descargó antes
        if verificar_descarga_previa(id_libro, email_destino):
            return False, "Ya has descargado este libro anteriormente. Revisa tu correo."
        
        # Descargar PDF del servidor remoto
        pdf_bytes = descargar_pdf_remoto(archivo_pdf)
        
        if not pdf_bytes:
            return False, "No se pudo descargar el PDF del servidor remoto."
        
        # Crear mensaje
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG["remitente"]
        msg['To'] = email_destino
        msg['Subject'] = f"Tu libro: {titulo_libro}"
        
        cuerpo = f"""Estimado lector,

Gracias por tu interés en "{titulo_libro}".

Adjunto encontrarás el archivo PDF para que disfrutes de la lectura.

Espero que el viaje por este universo te sea fascinante. Si quieres compartir tus impresiones, no dudes en responder a este correo.

Saludos desde el espacio,

El autor
---
Esta obra está protegida por derechos de autor.
Si no solicitaste este libro, ignora este mensaje."""
        
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        
        # Adjuntar PDF
        pdf_adjunto = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_adjunto.add_header('Content-Disposition', 'attachment', filename=f"{titulo_libro}.pdf")
        msg.attach(pdf_adjunto)
        
        # Enviar
        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["remitente"], EMAIL_CONFIG["password"])
            server.send_message(msg)
        
        # Registrar descarga exitosa
        registrar_descarga(id_libro, email_destino, exito=True)
        
        return True, "PDF enviado a tu correo electrónico. ¡Disfruta la lectura!"
    
    except Exception as e:
        registrar_descarga(id_libro, email_destino, exito=False)
        return False, f"Error al enviar: {str(e)}"

# -----------------------------
# FUNCIÓN PARA CARGAR Y MOSTRAR FOTO
# -----------------------------
def mostrar_foto_autor():
    """Carga y muestra la foto del autor"""
    # Buscar la foto en diferentes ubicaciones posibles
    posibles_rutas = [
        "fotorecortada9.jpg",
        "images/fotorecortada9.jpg",
        "img/fotorecortada9.jpg",
        "assets/fotorecortada9.jpg",
        "fotos/fotorecortada9.jpg"
    ]
    
    foto_encontrada = None
    for ruta in posibles_rutas:
        if os.path.exists(ruta):
            foto_encontrada = ruta
            break
    
    if foto_encontrada:
        try:
            foto = Image.open(foto_encontrada)
            return foto
        except Exception as e:
            st.warning(f"No se pudo cargar la foto: {str(e)}")
            return None
    else:
        # Si no se encuentra la foto, mostrar un placeholder
        st.info("📸 Foto del autor no encontrada. Coloca 'fotorecortada9.jpg' en el directorio principal.")
        return None

# -----------------------------
# ESTILOS CSS
# -----------------------------
st.markdown("""
<style>
    .stApp {
        background-color: #f5f0e8;
    }
    
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600&family=Inter:wght@300;400&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    h1, h2, h3 {
        font-family: 'Cormorant Garamond', serif;
        font-weight: 500;
        color: #2c2418;
    }
    
    h1 {
        font-size: 3rem;
        border-bottom: 1px solid #d4c9b8;
        padding-bottom: 0.5rem;
    }
    
    .book-entry {
        border-bottom: 1px solid #e0d6c8;
        padding: 1.5rem 0;
    }
    
    .book-title {
        font-family: 'Cormorant Garamond', serif;
        font-size: 1.5rem;
        font-weight: 600;
        color: #2c2418;
        cursor: pointer;
    }
    
    .book-title:hover {
        color: #6a5f4e;
        text-decoration: underline;
    }
    
    .book-meta {
        font-size: 0.85rem;
        color: #8a7f6e;
        margin-bottom: 0.75rem;
    }
    
    .abstract {
        background-color: #faf8f4;
        padding: 1rem;
        margin: 1rem 0;
        border-left: 3px solid #c4b8a8;
        font-size: 0.9rem;
        line-height: 1.5;
    }
    
    .descargas {
        font-size: 0.8rem;
        color: #6a5f4e;
        margin-top: 0.5rem;
    }
    
    .autor-container {
        display: flex;
        gap: 2rem;
        align-items: flex-start;
        margin: 2rem 0;
        padding: 1.5rem;
        background-color: #faf8f4;
        border-left: 3px solid #c4b8a8;
    }
    
    .autor-foto {
        flex-shrink: 0;
    }
    
    .autor-texto {
        flex: 1;
    }
    
    .autor-nombre {
        font-family: 'Cormorant Garamond', serif;
        font-size: 2rem;
        font-weight: 600;
        color: #2c2418;
        margin-bottom: 0.5rem;
    }
    
    .autor-bio {
        line-height: 1.6;
        color: #3a3226;
    }
    
    .stButton > button {
        background: none;
        border: 1px solid #c4b8a8;
        border-radius: 0;
        color: #5a4f3e;
        font-family: 'Inter', sans-serif;
        font-size: 0.8rem;
    }
    
    .stButton > button:hover {
        background: #e8e0d4;
        border-color: #9a8e7c;
    }
    
    .stTextInput > div > div > input {
        background-color: #faf8f4;
        border: 1px solid #e0d6c8;
        border-radius: 0;
    }
    
    .footer {
        font-size: 0.7rem;
        color: #b8ac98;
        text-align: center;
        margin-top: 3rem;
        padding-top: 1rem;
        border-top: 1px solid #e8e0d4;
    }
    
    hr {
        border-color: #e8e0d4;
    }
    
    .status-info {
        font-size: 0.8rem;
        color: #6a5f4e;
        text-align: center;
        margin-bottom: 1rem;
        padding: 0.5rem;
        background-color: #faf8f4;
        border-left: 3px solid #c4b8a8;
    }
    
    .foto-autor {
        border-radius: 5px;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------
# INICIALIZACIÓN
# -----------------------------
inicializar_csvs()

# -----------------------------
# SIDEBAR
# -----------------------------
with st.sidebar:
    st.markdown("### Navegación")
    opcion = st.radio(
        "Ir a:",
        ["Biblioteca", "Sobre el autor", "Admin"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    st.markdown("### Contacto")
    st.markdown("[polanco@unam.mx](mailto:polanco@unam.mx)")

# -----------------------------
# HEADER
# -----------------------------
st.markdown("# Biblioteca de Ciencia Ficción")
st.markdown("### *Relatos del espacio y la consciencia*")

# -----------------------------
# BIBLIOTECA
# -----------------------------
if opcion == "Biblioteca":
    st.markdown("## Libros disponibles")
    st.markdown("*Haz clic en el título para leer el abstract. Recibirás el PDF por correo.*")
    
    # Botón de sincronización
    if st.button("🔄 Sincronizar con biblioteca remota", key="sync_btn"):
        with st.spinner("Sincronizando libros del servidor remoto..."):
            success, msg = sincronizar_libros_remotos()
            if success:
                st.success(msg)
            else:
                st.error(msg)
    
    # Cargar libros
    libros = cargar_libros_desde_csv()
    
    if not libros:
        st.info("No hay libros disponibles. Usa el botón 'Sincronizar' para cargar los libros del servidor remoto.")
    else:
        # Inicializar estados de sesión para abstracts
        for libro in libros:
            key_abstract = f"show_abstract_{libro['id']}"
            if key_abstract not in st.session_state:
                st.session_state[key_abstract] = False
        
        # Mostrar libros
        for libro in libros:
            col_titulo, col_email = st.columns([2, 1])
            
            with col_titulo:
                # Título clickeable
                if st.button(libro['titulo'], key=f"btn_{libro['id']}"):
                    st.session_state[f"show_abstract_{libro['id']}"] = not st.session_state.get(f"show_abstract_{libro['id']}", False)
                
                st.markdown(f"<div class='book-meta'>ID: {libro['id']} · {libro['autor']} · {libro['año']}</div>", unsafe_allow_html=True)
                
                # Mostrar estadísticas de descargas
                num_descargas = obtener_estadisticas_libro(libro['id'])
                st.markdown(f"<div class='descargas'>📊 {num_descargas} descargas</div>", unsafe_allow_html=True)
            
            with col_email:
                email = st.text_input("Correo electrónico", key=f"email_{libro['id']}", 
                                      placeholder="tu@email.com", label_visibility="collapsed")
                if st.button("📧 Recibir PDF", key=f"enviar_{libro['id']}"):
                    if email and "@" in email:
                        # Verificar si ya descargó
                        if verificar_descarga_previa(libro['id'], email):
                            st.warning("Ya has descargado este libro anteriormente. Revisa tu correo.")
                        else:
                            with st.spinner("Conectando con biblioteca remota..."):
                                success, msg = enviar_pdf_por_email(email, libro['id'], libro['archivo_pdf'], libro['titulo'])
                                if success:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                    else:
                        st.warning("Por favor, ingresa un correo válido.")
            
            # Mostrar abstract
            if st.session_state.get(f"show_abstract_{libro['id']}", False):
                st.markdown(f"<div class='abstract'><strong>📖 Abstract</strong><br><br>{libro['abstract']}</div>", unsafe_allow_html=True)
            
            st.markdown("<div class='book-entry'></div>", unsafe_allow_html=True)

# -----------------------------
# SOBRE EL AUTOR (MODIFICADO CON FOTO)
# -----------------------------
elif opcion == "Sobre el autor":
    st.markdown("## Sobre el autor")
    
    # Cargar foto
    foto = mostrar_foto_autor()
    
    if foto:
        # Mostrar foto y texto en dos columnas
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.image(foto, caption="Autor", use_container_width=True)
        
        with col2:
            st.markdown("""
            ### **Escritor de ciencia ficción**
            
            Escribo ciencia ficción porque creo que los mejores futuros posibles empiezan con preguntas incómodas en el presente.
            
            Mis historias exploran:
            - El aislamiento y la conexión en el espacio profundo
            - Los límites de la memoria y la identidad
            - Lo que queda de humano cuando la tecnología lo envuelve todo
            """)
    else:
        # Si no hay foto, mostrar solo texto
        st.markdown("""
        ### **Escritor de ciencia ficción**
        
        Escribo ciencia ficción porque creo que los mejores futuros posibles empiezan con preguntas incómodas en el presente.
        
        Mis historias exploran:
        - El aislamiento y la conexión en el espacio profundo
        - Los límites de la memoria y la identidad
        - Lo que queda de humano cuando la tecnología lo envuelve todo
        """)
    
    # Texto adicional que se muestra siempre
    st.markdown("""
    ---
    
    **Trayectoria literaria:**
    
    He publicado en diversas antologías y revistas de género. Mi trabajo se ha centrado en construir universos donde la ciencia y la emoción humana colisionan de formas inesperadas.
    
    **Filosofía de escritura:**
    
    > "La mejor ciencia ficción no predice el futuro, sino que revela verdades incómodas sobre nuestro presente vestidas de naves espaciales y alienígenas."
    
    ---
    
    **Contacto y colaboraciones:**
    
    Para prensa, entrevistas o colaboraciones literarias, escríbeme a:
    [polanco@unam.mx](mailto:polanco@unam.mx)
    
    **Sígueme en:**
    - Bluesky: [@autor.bsky.social](https://bsky.app)
    - Goodreads: [Autor en Goodreads](https://goodreads.com)
    """)

# -----------------------------
# ADMIN
# -----------------------------
elif opcion == "Admin":
    st.markdown("## Panel de administración")
    
    tab1, tab2, tab3 = st.tabs(["📚 Libros", "📊 Descargas", "➕ Agregar libro"])
    
    with tab1:
        st.markdown("### Catálogo de libros")
        if os.path.exists(CSV_LIBROS_FILE):
            df_libros = pd.read_csv(CSV_LIBROS_FILE)
            if len(df_libros) > 0:
                # Mostrar libros
                for idx, libro in df_libros.iterrows():
                    with st.expander(f"ID {libro['id']}: {libro['titulo']}"):
                        st.markdown(f"**Autor:** {libro['autor']}")
                        st.markdown(f"**Año:** {libro['año']}")
                        st.markdown(f"**Archivo PDF:** `{libro['archivo_pdf']}`")
                        st.markdown(f"**Sinopsis:** {libro['sinopsis']}")
                        st.markdown(f"**Abstract:** {libro['abstract']}")
                        
                        # Mostrar descargas de este libro
                        if os.path.exists(CSV_LECTURAS_FILE):
                            df_lecturas = pd.read_csv(CSV_LECTURAS_FILE)
                            descargas_libro = df_lecturas[df_lecturas['id_libro'] == libro['id']]
                            st.markdown(f"**Total descargas:** {len(descargas_libro)}")
                st.markdown(f"**Total libros:** {len(df_libros)}")
            else:
                st.info("No hay libros registrados.")
        else:
            st.info("No hay libros registrados.")
    
    with tab2:
        st.markdown("### Registro de descargas")
        if os.path.exists(CSV_LECTURAS_FILE):
            df_lecturas = pd.read_csv(CSV_LECTURAS_FILE)
            if len(df_lecturas) > 0:
                # Unir con información de libros
                if os.path.exists(CSV_LIBROS_FILE):
                    df_libros = pd.read_csv(CSV_LIBROS_FILE)
                    df_completo = df_lecturas.merge(df_libros[['id', 'titulo']], left_on='id_libro', right_on='id', how='left')
                    st.dataframe(df_completo[['fecha', 'email', 'titulo', 'exito', 'ip']])
                    
                    # Estadísticas
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total descargas", len(df_lecturas))
                    with col2:
                        st.metric("Descargas exitosas", len(df_lecturas[df_lecturas['exito'] == True]))
                    with col3:
                        st.metric("Usuarios únicos", df_lecturas['email'].nunique())
                    
                    # Botón para descargar CSV
                    csv = df_lecturas.to_csv(index=False)
                    st.download_button("📥 Descargar CSV de descargas", csv, "registro_descargas.csv", "text/csv")
                else:
                    st.dataframe(df_lecturas)
            else:
                st.info("No hay registros de descargas.")
        else:
            st.info("No hay registros de descargas.")
    
    with tab3:
        st.markdown("### Agregar nuevo libro manualmente")
        with st.form("nuevo_libro_form"):
            titulo = st.text_input("Título")
            autor = st.text_input("Autor")
            año = st.number_input("Año", min_value=1900, max_value=2030, value=datetime.now().year)
            sinopsis = st.text_area("Sinopsis")
            abstract = st.text_area("Abstract")
            archivo_pdf = st.text_input("Nombre del archivo PDF (debe existir en el servidor remoto)")
            
            submitted = st.form_submit_button("Guardar libro")
            if submitted:
                if titulo and archivo_pdf:
                    nuevo_libro = {
                        'id': None,  # Se asignará automáticamente
                        'titulo': titulo,
                        'autor': autor,
                        'año': año,
                        'sinopsis': sinopsis,
                        'archivo_pdf': archivo_pdf,
                        'abstract': abstract
                    }
                    nuevo_id = guardar_libro_en_csv(nuevo_libro)
                    if nuevo_id:
                        st.success(f"Libro guardado con ID: {nuevo_id}")
                    else:
                        st.error("Error al guardar el libro")
                else:
                    st.warning("Título y archivo PDF son obligatorios")

# -----------------------------
# FOOTER
# -----------------------------
st.markdown('<div class="footer">© 2026 · Biblioteca de Ciencia Ficción · Sistema de gestión bibliotecaria</div>', 
            unsafe_allow_html=True)
