from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import current_marketplace_user, optional_marketplace_user, roles_required
from app.core.config import get_settings
from app.database.session import get_db
from app.models import MarketplaceOrder, MarketplaceOrderItem, MarketplaceSession, MarketplaceUser, Product, ShoppingCart
from app.schemas.marketplace import (
    AuthLogin,
    AuthRegister,
    CartItemAdd,
    CartItemUpdate,
    CheckoutRequest,
    ProductCreate,
    ProductSearch,
    ProductUpdate,
)
from app.services import paypal
from app.services.auth import SESSION_COOKIE, create_session, hash_password, public_user, session_for_token, verify_password
from app.services.marketplace import (
    add_to_cart,
    cart_summary,
    create_marketplace_order,
    create_merchant_product,
    expire_stale_orders,
    get_product,
    list_categories,
    merchant_products,
    order_out,
    product_image_url,
    product_out,
    remove_cart_item,
    search_products,
    update_cart_item,
    update_merchant_product,
    validate_cart,
)


router = APIRouter(prefix="/api")


def _set_auth_cookie(response: Response, request: Request, token: str, expires_at: datetime) -> None:
    seconds = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=seconds,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


@router.post("/auth/register", status_code=201)
def register(payload: AuthRegister, request: Request, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(MarketplaceUser.id).where(MarketplaceUser.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    user = MarketplaceUser(
        full_name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
        token, expires_at = create_session(db, user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with that email already exists") from exc
    _set_auth_cookie(response, request, token, expires_at)
    return {"user": public_user(user)}


@router.post("/auth/login")
def login(payload: AuthLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(MarketplaceUser).where(MarketplaceUser.email == payload.email))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    token, expires_at = create_session(db, user)
    _set_auth_cookie(response, request, token, expires_at)
    return {"user": public_user(user)}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    session = session_for_token(db, token)
    if session is not None:
        db.delete(session)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    response.status_code = 204
    response.body = b""
    return None


@router.get("/auth/me")
def me(user: MarketplaceUser | None = Depends(optional_marketplace_user)):
    return {"authenticated": user is not None, "user": public_user(user) if user else None}


@router.get("/marketplace/categories")
def categories(db: Session = Depends(get_db)):
    return {"items": list_categories(db)}


@router.post("/marketplace/products/search")
def products_search(payload: ProductSearch, db: Session = Depends(get_db)):
    return search_products(db, payload)


@router.get("/marketplace/products/{slug}")
def product_detail(slug: str, db: Session = Depends(get_db)):
    return product_out(get_product(db, slug))


@router.get("/marketplace/cart")
def cart(user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    expire_stale_orders(db)
    return cart_summary(db, user.id)


@router.post("/marketplace/cart/items")
def cart_add(payload: CartItemAdd, user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    return add_to_cart(db, user, payload.product_id, payload.quantity)


@router.put("/marketplace/cart/items/{item_id}")
def cart_update(item_id: int, payload: CartItemUpdate, user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    return update_cart_item(db, user, item_id, payload.quantity)


@router.delete("/marketplace/cart/items/{item_id}")
def cart_remove(item_id: int, user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    return remove_cart_item(db, user, item_id)


@router.post("/marketplace/checkout/quote")
def checkout_quote(_payload: CheckoutRequest, user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    expire_stale_orders(db)
    _cart, lines, totals = validate_cart(db, user.id)
    return {
        "item_count": sum(line["quantity"] for line in lines),
        "currency": totals["currency"],
        "subtotal": float(totals["subtotal"]),
        "tax_rate": float(totals["tax_rate"]),
        "tax": float(totals["tax"]),
        "shipping": float(totals["shipping"]),
        "total": float(totals["total"]),
    }


def _create_paypal_cart_checkout(request: Request, user: MarketplaceUser, db: Session):
    if not paypal.enabled():
        raise HTTPException(status_code=503, detail="PayPal Sandbox is not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to the server-side .env file.")
    expire_stale_orders(db)
    order = create_marketplace_order(db, user.id)
    try:
        return paypal.create_order(db, order, str(request.base_url).rstrip("/"))
    except Exception:
        db.rollback()
        raise


@router.post("/marketplace/checkout/paypal")
def marketplace_paypal_checkout(
    _payload: CheckoutRequest,
    request: Request,
    user: MarketplaceUser = Depends(roles_required("customer")),
    db: Session = Depends(get_db),
):
    return _create_paypal_cart_checkout(request, user, db)


@router.post("/paypal/sandbox/orders")
def paypal_sandbox_checkout_compat(
    _payload: CheckoutRequest,
    request: Request,
    user: MarketplaceUser = Depends(roles_required("customer")),
    db: Session = Depends(get_db),
):
    """Retain the V3 path, but never accept a browser-supplied price."""
    return _create_paypal_cart_checkout(request, user, db)


@router.post("/paypal/sandbox/orders/{paypal_order_id}/capture")
def paypal_capture(
    paypal_order_id: str,
    user: MarketplaceUser = Depends(roles_required("customer")),
    db: Session = Depends(get_db),
):
    return paypal.capture_order(db, paypal_order_id, user.id)


@router.post("/paypal/sandbox/orders/{paypal_order_id}/cancel")
def paypal_cancel(
    paypal_order_id: str,
    user: MarketplaceUser = Depends(roles_required("customer")),
    db: Session = Depends(get_db),
):
    return paypal.cancel_order(db, paypal_order_id, user.id)


@router.get("/marketplace/orders")
def customer_orders(user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(MarketplaceOrder)
        .options(selectinload(MarketplaceOrder.items).selectinload(MarketplaceOrderItem.product))
        .where(MarketplaceOrder.user_id == user.id)
        .order_by(MarketplaceOrder.created_at.desc())
    )
    return {"items": [order_out(row) for row in rows]}


@router.get("/marketplace/orders/{order_number}")
def customer_order_detail(order_number: str, user: MarketplaceUser = Depends(roles_required("customer")), db: Session = Depends(get_db)):
    order = db.scalar(
        select(MarketplaceOrder)
        .options(selectinload(MarketplaceOrder.items).selectinload(MarketplaceOrderItem.product))
        .where(MarketplaceOrder.order_number == order_number, MarketplaceOrder.user_id == user.id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order_out(order)


@router.get("/merchant/products")
def merchant_product_list(user: MarketplaceUser = Depends(roles_required("merchant")), db: Session = Depends(get_db)):
    return {"items": merchant_products(db, user.id)}


@router.post("/merchant/products", status_code=201)
def merchant_product_create(payload: ProductCreate, user: MarketplaceUser = Depends(roles_required("merchant")), db: Session = Depends(get_db)):
    return {"product": create_merchant_product(db, user, payload)}


@router.put("/merchant/products/{product_id}")
def merchant_product_update(product_id: int, payload: ProductUpdate, user: MarketplaceUser = Depends(roles_required("merchant")), db: Session = Depends(get_db)):
    return {"product": update_merchant_product(db, user, product_id, payload)}


@router.post("/merchant/products/{product_id}/image")
def merchant_product_image_upload(
    product_id: int,
    file: UploadFile = File(...),
    user: MarketplaceUser = Depends(roles_required("merchant")),
    db: Session = Depends(get_db),
):
    """Store an owned merchant product image as a normalized, randomly named WebP."""
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.merchant))
        .where(Product.id == product_id, Product.merchant_id == user.id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    settings = get_settings()
    raw = file.file.read(settings.marketplace_max_image_bytes + 1)
    if len(raw) > settings.marketplace_max_image_bytes:
        file.file.close()
        limit_mb = max(1, settings.marketplace_max_image_bytes // (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"Image is too large. Choose an image under {limit_mb} MB.")
    if not raw:
        file.file.close()
        raise HTTPException(status_code=422, detail="Choose an image file to upload.")

    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP image.")
            if source.width < 32 or source.height < 32 or source.width * source.height > 20_000_000:
                raise HTTPException(status_code=422, detail="Image dimensions must be between 32 px and 20 megapixels.")
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(status_code=422, detail="The uploaded file is not a valid supported image.") from exc
    finally:
        file.file.close()

    old_path = product.image_path
    upload_dir = Path(settings.product_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.webp"
    target = upload_dir / filename
    try:
        target.write_bytes(output.getvalue())
        product.image_path = f"/media/{filename}"
        db.commit()
        db.refresh(product)
    except Exception as exc:
        db.rollback()
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The product image could not be saved.") from exc

    return {"product": product_out(product), "image_url": product_image_url(product)}
