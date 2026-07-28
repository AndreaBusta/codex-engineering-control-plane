# Recuperación segura

## Regla principal

Cuando un cambio “desaparece”, dejar de mutar el repositorio. Git suele conservar
el objeto aunque ya no exista una referencia visible.

No ejecutar:

- `reset --hard`;
- limpieza destructiva;
- borrado de worktrees;
- garbage collection agresiva;
- force push;
- restauración masiva.

## Inventario

Registrar:

```bash
pwd
git rev-parse --show-toplevel
git worktree list --porcelain
git status --short --branch
git branch --all --verbose --no-abbrev
git log --all --decorate --oneline --graph -40
git reflog --all --date=iso
```

No pegar secretos o URLs con credenciales en informes.

## Diagnóstico

Preguntas:

- ¿se modificó pero no se hizo commit?
- ¿el commit está en otra rama?
- ¿se trabajó en detached HEAD?
- ¿el merge fue local?
- ¿faltó push?
- ¿se abrió otro worktree?
- ¿el PR usó squash?
- ¿Xcode compiló otra carpeta?
- ¿se reutilizó un artefacto?

## Proteger antes de separar

Si se identifica un commit valioso, crear una referencia de rescate solo cuando
esté autorizado:

```text
rescue/<fecha>-<frente> → <commit>
```

Comprobar que la referencia apunta al hash exacto antes de cualquier otra
operación.

## Objetos no referenciados

Después del reflog:

```bash
git fsck --no-reflogs --unreachable
```

Inspeccionar candidatos con `git show --stat --oneline <hash>`. No aplicar nada
hasta entender origen, fecha y diff.

## Rama multifrente

1. capturar estado actual;
2. mapear hunk → unidad;
3. localizar cambios compartidos;
4. diseñar orden;
5. crear ramas desde una base segura;
6. cherry-pick por commits coherentes o reaplicar hunks con revisión;
7. comparar la suma con el rescate;
8. ejecutar gates de cada unidad.

Evitar `cherry-pick` indiscriminado si un commit mezcla varios frentes.

## Recuperación de remoto

Comparar:

- HEAD local;
- rama remota de feature;
- PR;
- merge commit;
- `origin/<base>`.

Con squash merge, el hash de feature puede no aparecer en base. Consultar el PR
y verificar su merge commit.

## Recuperación de release

Separar código y artefacto:

- commit correcto;
- workflow correcto;
- build correcta;
- estado del proveedor.

Si el código está en base pero la build no, crear una nueva release autorizada;
no modificar Git para imitar el artefacto.

## Cierre de recuperación

Entregar:

- causa;
- estado preservado;
- commits recuperados;
- ramas creadas;
- evidencia de no pérdida;
- acciones destructivas evitadas;
- prevención.
