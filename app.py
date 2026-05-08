import os
from flask import Flask, redirect, url_for, session, render_template, request, abort
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
from models import db, User, Post, Comment
from dotenv import load_dotenv
import google.generativeai as genai
import json
import nh3
import re
from datetime import datetime

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

db.init_app(app)

# Gemini AI setup
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model_name = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash-latest')
model = genai.GenerativeModel(model_name)

# OAuth setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

@app.before_request
def create_tables():
    # Only create tables once
    if not hasattr(app, '_db_initialized'):
        db.create_all()
        
        # Seed initial data if database is empty
        if User.query.count() == 0:
            # Create the primary admin user
            admin = User(
                email='tobanature@gmail.com',
                name='Swandi',
                google_id='initial_admin',
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()

            # Create the example post
            example_post = Post(
                title='Tantangan Pemerintah Daerah Menghadapi Efisiensi Era Prabowo',
                content="""Kebijakan efisiensi anggaran yang dicanangkan oleh Presiden Prabowo Subianto menjadi tantangan serius bagi Pemerintah Daerah (Pemda). Di tengah tuntutan untuk mengoptimalkan pelayanan publik, Pemda kini dihadapkan pada restrukturisasi pos anggaran yang lebih ketat.

Salah satu poin utama adalah pemangkasan perjalanan dinas dan kegiatan seremonial yang selama ini mendominasi belanja daerah. Tantangannya terletak pada bagaimana Pemda tetap mampu menjalankan roda pemerintahan tanpa mengurangi kualitas layanan kepada masyarakat.

Selain itu, efisiensi ini menuntut digitalisasi birokrasi yang lebih masif. Pemda yang belum siap secara infrastruktur digital akan kesulitan beradaptasi dengan ritme kerja cepat dan hemat yang diinginkan pusat. Pertanyaannya, apakah Pemda siap melakukan reformasi struktural demi keberlanjutan fiskal nasional?""",
                author_id=admin.id
            )
            db.session.add(example_post)
            db.session.commit()

        app._db_initialized = True

# Helper to check login
def get_current_user():
    if 'user' in session:
        return User.query.get(session['user']['id'])
    return None

@app.context_processor
def inject_user():
    return dict(user=get_current_user())

@app.template_filter('clean_empty_p')
def clean_empty_p(content):
    if not content:
        return ""
    return re.sub(r'<p>\s*(?:<br\s*/?>)?\s*</p>', '', content)

@app.route('/')
def index():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('index.html', posts=posts)

@app.route('/post/<int:post_id>')
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    # Fetch only top-level comments
    comments = Comment.query.filter_by(post_id=post_id, parent_id=None).order_by(Comment.created_at.asc()).all()
    return render_template('post.html', post=post, comments=comments)

@app.route('/post/<int:post_id>/comment', methods=['POST'])
def add_comment(post_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    content = request.form.get('content')
    parent_id = request.form.get('parent_id')
    
    if content:
        comment = Comment(
            content=content, 
            user_id=user.id, 
            post_id=post_id,
            parent_id=int(parent_id) if parent_id else None
        )
        db.session.add(comment)
        db.session.commit()
    
    return redirect(url_for('post_detail', post_id=post_id))

@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
    user_info = resp.json()
    
    user = User.query.filter_by(email=user_info['email']).first()
    if not user:
        # First time login
        role = 'admin' if user_info['email'] == 'tobanature@gmail.com' else 'visitor'
        user = User(
            email=user_info['email'],
            name=user_info.get('name'),
            google_id=user_info.get('sub'),
            role=role
        )
        db.session.add(user)
        db.session.commit()
    elif user.email == 'tobanature@gmail.com' and user.role != 'admin':
        # Auto-upgrade if not already admin
        user.role = 'admin'
        db.session.commit()
    
    session['user'] = {'id': user.id, 'email': user.email, 'role': user.role}
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

@app.route('/profile')
def profile():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    # If the user is an admin, show their posts
    user_posts = []
    if user.role == 'admin':
        user_posts = Post.query.filter_by(author_id=user.id).order_by(Post.created_at.desc()).all()
        
    return render_template('profile.html', user=user, posts=user_posts)

@app.route('/admin')
def admin():
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('admin.html', posts=posts)

@app.route('/admin/post/new', methods=['GET', 'POST'])
def new_post():
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    if request.method == 'POST':
        title = request.form.get('title')
        raw_content = request.form.get('content')
        # Sanitize HTML
        content = nh3.clean(raw_content, tags={'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'blockquote', 'pre', 'ul', 'ol', 'li', 'a', 'img'}, attributes={'a': {'href'}, 'img': {'src', 'alt'}})
        
        # Remove empty paragraphs
        content = re.sub(r'<p>\s*(?:<br\s*/?>)?\s*</p>', '', content)
        
        post = Post(title=title, content=content, author_id=user.id)
        
        created_at_str = request.form.get('created_at')
        if created_at_str:
            try:
                post.created_at = datetime.fromisoformat(created_at_str)
            except ValueError:
                pass

        db.session.add(post)
        db.session.commit()
        return redirect(url_for('admin'))
    
    return render_template('edit_post.html', post=None)

@app.route('/admin/post/edit/<int:post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    post = Post.query.get_or_404(post_id)
    if request.method == 'POST':
        post.title = request.form.get('title')
        raw_content = request.form.get('content')
        # Sanitize HTML
        post.content = nh3.clean(raw_content, tags={'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'blockquote', 'pre', 'ul', 'ol', 'li', 'a', 'img'}, attributes={'a': {'href'}, 'img': {'src', 'alt'}})
        
        # Remove empty paragraphs
        post.content = re.sub(r'<p>\s*(?:<br\s*/?>)?\s*</p>', '', post.content)
        
        created_at_str = request.form.get('created_at')
        if created_at_str:
            try:
                post.created_at = datetime.fromisoformat(created_at_str)
            except ValueError:
                pass

        db.session.commit()
        return redirect(url_for('admin'))
    
    return render_template('edit_post.html', post=post)

@app.route('/admin/post/delete/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    post = Post.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/comments')
def admin_comments():
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    comments = Comment.query.order_by(Comment.created_at.desc()).all()
    return render_template('admin_comments.html', comments=comments)

@app.route('/admin/comment/delete/<int:comment_id>', methods=['POST'])
def admin_delete_comment(comment_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    comment = Comment.query.get_or_404(comment_id)
    db.session.delete(comment)
    db.session.commit()
    return redirect(url_for('admin_comments'))

@app.route('/admin/upload-image', methods=['POST'])
def upload_image():
    user = get_current_user()
    if not user or user.role != 'admin':
        return {"error": "Unauthorized"}, 403
    
    if 'image' not in request.files:
        return {"error": "No file part"}, 400
    
    file = request.files['image']
    if file.filename == '':
        return {"error": "No selected file"}, 400
    
    if file:
        filename = secure_filename(file.filename)
        # Add timestamp to filename to avoid collisions
        filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return {"url": url_for('static', filename=f'uploads/{filename}')}

@app.route('/admin/ai-assist', methods=['POST'])
def ai_assist():
    user = get_current_user()
    if not user or user.role != 'admin':
        return abort(403)
    
    data = request.json
    prompt = data.get('prompt')
    current_content = data.get('content', '')
    title = data.get('title', '')

    system_prompt = f"""Anda adalah editor dan penulis blog profesional berkaliber tinggi.
Tugas Anda adalah mengembangkan, memperbaiki, atau menulis ulang konten berdasarkan instruksi pengguna dengan gaya bahasa Indonesia yang menarik dan informatif.

<Konteks>
Judul Artikel: {title}
Konten Saat Ini: {current_content}
</Konteks>

<Instruksi_Pengguna>
{prompt}
</Instruksi_Pengguna>

<Aturan_Penulisan>
1. SUBSTANSI: Pertahankan makna dan pesan utama dari konten asli. Perkaya dengan informasi relevan yang bermanfaat bagi pembaca.
2. STRUKTUR: Buat paragraf yang padat dan berisi (idealnya 3-5 kalimat per paragraf). Hindari paragraf satu kalimat.
3. FORMAT: Output WAJIB berupa HTML murni (gunakan tag <p>, <strong>, <em>, <h2>, <h3>, <ul>, <li> sesuai kebutuhan artikel).
4. RESTRUKSI: DILARANG KERAS membungkus output dengan markdown code blocks (seperti ```html).
5. RESTRUKSI: Jawablah LANGSUNG dengan hasil konten HTML. DILARANG KERAS menggunakan kalimat basa-basi seperti "Berikut drafnya", "Tentu", dll.
</Aturan_Penulisan>"""

    try:
        response = model.generate_content(system_prompt)
        return {"content": response.text}
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8082, debug=True)
