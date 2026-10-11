# DMC - IATHON

Base del frontend para la réplica de DMC Institute.

## Estructura

- `frontend/`: interfaz visual del proyecto. Esta carpeta puede trabajarse sin tocar la futura lógica de IA.
- `ai-service/`: reservado para la integración del asistente/IA del equipo.

## Ejecutar localmente

### Opción 1: Python

```bash
git clone https://github.com/SAEZ1205/DMC---AITHON.git
cd DMC---AITHON\frontend
python -m http.server 5500
```

Luego abre en el navegador:

`http://localhost:5500`

### Opción 2: Node.js

```bash
git clone https://github.com/SAEZ1205/DMC---AITHON.git
cd DMC---AITHON\frontend
npx serve .
```

## Pantallas actuales

- `index.html`: login estilo DMC Institute.
- `dashboard.html`: área personal con menú lateral, barra superior, menú de perfil y curso reciente.

Por ahora el login es solo demostrativo: cualquier usuario/contraseña o el botón de invitado abre el área personal. La conexión real se hará posteriormente mediante API/backend.
