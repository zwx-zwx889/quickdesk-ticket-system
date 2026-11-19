"""Authentication and role helpers for QuickDesk."""

from contextlib import closing
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from quickdesk.db import connect_db


auth_bp = Blueprint(
    "auth",
    __name__,
)


ROLE_LABELS = {
    "admin": "管理员",
    "requester": "工单提交人",
    "assignee": "工单处理人",
}


@auth_bp.before_app_request
def load_logged_in_user():
    """Load the current user before every request."""
    user_id = session.get("user_id")
    g.user = None

    if user_id is None:
        return

    with closing(connect_db()) as conn:
        g.user = conn.execute(
            """
            SELECT
                id,
                username,
                display_name,
                role,
                department,
                active,
                must_change_password
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    if g.user is None or not g.user["active"]:
        session.clear()
        g.user = None
        return

    if (
        g.user["must_change_password"]
        and request.endpoint not in {
            "auth.change_password",
            "auth.logout",
            "static",
        }
    ):
        return redirect(url_for("auth.change_password"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a QuickDesk user."""
    if g.user is not None:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get(
            "username",
            "",
        ).strip()

        password = request.form.get(
            "password",
            "",
        )

        with closing(connect_db()) as conn:
            user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()

        error = None

        if user is None:
            error = "用户名或密码错误！"
        elif not user["active"]:
            error = "该用户已被停用！"
        elif not check_password_hash(
            user["password_hash"],
            password,
        ):
            error = "用户名或密码错误！"

        if error is None:
            session.clear()
            session["user_id"] = user["id"]

            if user["must_change_password"]:
                flash(
                    "当前使用的是临时密码，请先设置新密码。",
                    "info",
                )
                return redirect(
                    url_for("auth.change_password")
                )

            role_label = ROLE_LABELS.get(
                user['role'],
                user['role'],
            )

            flash(
                (
                    f'欢迎登录：{role_label} · '
                    f'{user["department"] or "未设置部门"} · '
                    f'{user["display_name"]}！'
                ),
                'success',
            )

            return redirect(url_for("index"))

        flash(error, "error")

    return render_template(
        "login.html",
        role_labels=ROLE_LABELS,
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """End the current user session."""
    session.clear()
    flash("您已安全退出系统。", "info")
    return redirect(url_for("auth.login"))


def login_required(view):
    """Require an authenticated user."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))

        return view(*args, **kwargs)

    return wrapped_view


@auth_bp.route(
    "/change-password",
    methods=["GET", "POST"],
)
@login_required
def change_password():
    """Allow the current user to replace their password."""
    if request.method == "POST":
        current_password = request.form.get(
            "current_password",
            "",
        )
        new_password = request.form.get(
            "new_password",
            "",
        )
        confirm_password = request.form.get(
            "confirm_password",
            "",
        )

        with closing(connect_db()) as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (g.user["id"],),
            ).fetchone()

            error = None

            if not check_password_hash(
                user["password_hash"],
                current_password,
            ):
                error = "当前密码不正确！"
            elif len(new_password) < 8:
                error = "新密码长度不能少于8位！"
            elif new_password != confirm_password:
                error = "两次输入的新密码不一致！"
            elif new_password == current_password:
                error = "新密码不能与当前密码相同！"

            if error is None:
                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?,
                        must_change_password = 0,
                        password_changed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        generate_password_hash(new_password),
                        g.user["id"],
                    ),
                )
                conn.commit()

                flash("密码修改成功！", "success")
                return redirect(url_for("index"))

        flash(error, "error")

    return render_template("change_password.html")


def roles_required(*allowed_roles):
    """Require one of the supplied user roles."""

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if g.user is None:
                flash("请先登录系统！", "error")
                return redirect(url_for("auth.login"))

            if g.user["role"] not in allowed_roles:
                abort(403)

            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def get_role_label(role):
    """Return the Chinese label for a role."""
    return ROLE_LABELS.get(
        role,
        role,
    )
