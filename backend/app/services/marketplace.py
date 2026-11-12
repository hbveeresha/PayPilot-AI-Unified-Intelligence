import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import (
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceUser,
    PayPalSandboxOrder,
    Product,
    ProductCategory,
    ShoppingCart,
    ShoppingCartItem,
)
from app.schemas.marketplace import ProductCreate, ProductSearch, ProductUpdate
from app.services.auth import hash_password


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _category_key(slug: str) -> str:
    return {
        "electronics": "tech",
        "home-kitchen": "home",
        "fashion": "style",
        "beauty-wellness": "wellness",
        "books-stationery": "books",
        "sports-outdoors": "outdoors",
        "mobiles": "tech",
        "laptops": "tech",
        "accessories": "tech",
        "gaming": "tech",
        "shoes": "style",
        "appliances": "home",
    }.get(slug, "essentials")


@lru_cache(maxsize=1)
def _catalog_merchant_password_hash() -> str:
    # The synthetic catalogue owner is not a login account; keep its random
    # password unrecorded while reusing its scrypt hash for repeatable seeding.
    return hash_password(secrets.token_urlsafe(32))


def category_out(category: ProductCategory) -> dict:
    return {"id": category.id, "name": category.name, "slug": category.slug, "description": category.description}


PRODUCT_IMAGE_FALLBACK = "/static/images/product-fallback.svg"


def product_image_url(product: Product | None, snapshot_path: str | None = None) -> str:
    path = snapshot_path or (product.image_path if product is not None else None)
    return path if path and path.startswith(("/static/images/products/", "/media/")) else PRODUCT_IMAGE_FALLBACK


def product_out(product: Product) -> dict:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "price": float(product.price),
        "currency": product.currency,
        "stock_quantity": product.stock_quantity,
        "in_stock": product.is_active and product.stock_quantity > 0,
        "image_key": product.image_key,
        "image_url": product_image_url(product),
        "rating": float(product.rating),
        "review_count": product.review_count,
        "category": category_out(product.category),
        "merchant_name": product.merchant.full_name,
    }


def seed_marketplace_catalog(db: Session) -> int:
    categories = [
        ("Electronics", "electronics", "Everyday tech, thoughtfully selected."),
        ("Home & kitchen", "home-kitchen", "Small upgrades for the spaces you love."),
        ("Fashion", "fashion", "Useful everyday pieces with lasting style."),
        ("Beauty & wellness", "beauty-wellness", "Simple rituals for your daily routine."),
        ("Books & stationery", "books-stationery", "Ideas, notes, and tools for curious minds."),
        ("Sports & outdoors", "sports-outdoors", "Gear for your next move outside."),
        ("Mobiles", "mobiles", "Smartphones and everyday mobile essentials."),
        ("Laptops", "laptops", "Portable computers for work, study, and creativity."),
        ("Shoes", "shoes", "Comfortable footwear for work, weekends, and movement."),
        ("Accessories", "accessories", "Finishing touches and useful tech accessories."),
        ("Gaming", "gaming", "Controllers and gear for play at home."),
        ("Appliances", "appliances", "Small appliances that make daily routines easier."),
    ]
    for order, (name, slug, description) in enumerate(categories):
        if db.scalar(select(ProductCategory.id).where(ProductCategory.slug == slug)) is None:
            db.add(ProductCategory(name=name, slug=slug, description=description, sort_order=order))
    merchant = db.scalar(select(MarketplaceUser).where(MarketplaceUser.email == "catalog@paypilot.test"))
    if merchant is None:
        merchant = MarketplaceUser(
            full_name="PayPilot Curated",
            email="catalog@paypilot.test",
            password_hash=_catalog_merchant_password_hash(),
            role="merchant",
            is_active=True,
        )
        db.add(merchant)
        db.flush()
    db.flush()
    category_map = {row.slug: row for row in db.scalars(select(ProductCategory))}
    products = [
        ("soundwave-mini-speaker", "Soundwave Mini Bluetooth Speaker", "Room-filling sound in a compact, water-resistant design with up to 12 hours of playtime.", "electronics", 39.00, 28, 4.8, 126, "tech"),
        ("orbit-wireless-headphones", "Orbit Wireless Headphones", "Comfort-fit over-ear headphones with clear calls, soft cushions, and a 30-hour battery.", "electronics", 79.00, 16, 4.7, 84, "tech"),
        ("studio-desk-lamp", "Studio Adjustable Desk Lamp", "A dimmable warm-to-cool LED desk lamp with a weighted base and USB-C power.", "home-kitchen", 34.50, 22, 4.6, 51, "home"),
        ("ceramic-pour-over-set", "Ceramic Pour-Over Coffee Set", "A glazed ceramic dripper and matching cup for a calm, balanced morning brew.", "home-kitchen", 28.00, 19, 4.9, 112, "home"),
        ("weekender-canvas-tote", "Weekender Canvas Tote", "A durable everyday carryall made from heavyweight cotton canvas with an inner pocket.", "fashion", 32.00, 35, 4.7, 93, "style"),
        ("everyday-crossbody", "Everyday Crossbody Bag", "A lightweight, adjustable crossbody with secure zip pockets for daily essentials.", "fashion", 46.00, 14, 4.5, 37, "style"),
        ("botanical-hand-care", "Botanical Hand Care Duo", "A gentle, plant-based hand wash and nourishing lotion in a reusable glass bottle set.", "beauty-wellness", 24.00, 40, 4.8, 205, "wellness"),
        ("daily-hydration-kit", "Daily Hydration Skincare Kit", "A simple three-step routine with fragrance-free cleanser, serum, and daily moisturizer.", "beauty-wellness", 42.00, 18, 4.6, 64, "wellness"),
        ("field-notes-journal", "Field Notes Hardcover Journal", "A lay-flat ruled notebook with a linen cover, ribbon marker, and recycled paper.", "books-stationery", 18.00, 64, 4.9, 310, "books"),
        ("focus-planner", "Focus Weekly Planner", "A flexible undated planner with weekly priorities, habit check-ins, and notes pages.", "books-stationery", 21.50, 31, 4.7, 148, "books"),
        ("trail-ready-bottle", "Trail-Ready Steel Bottle", "A vacuum-insulated stainless-steel bottle that keeps drinks cold for a full day.", "sports-outdoors", 27.00, 48, 4.8, 188, "outdoors"),
        ("stretch-mobility-band", "Stretch & Mobility Band Set", "Three resistance levels and a compact carry pouch for a steady at-home routine.", "sports-outdoors", 16.00, 53, 4.5, 76, "outdoors"),
        ("soft-knit-throw", "Soft Knit Lounge Throw", "A breathable, textured throw blanket made for a reading nook or a quiet evening.", "home-kitchen", 58.00, 11, 4.9, 44, "home"),
        ("portable-power-bank", "Pocket Portable Power Bank", "A slim 10,000 mAh power bank with USB-C fast charging and a clear battery indicator.", "electronics", 49.00, 24, 4.6, 132, "tech"),
        ("weekend-travel-pouch", "Weekend Travel Organizer Pouch", "A water-resistant organizer with easy-access pockets for cables and small essentials.", "fashion", 25.00, 26, 4.4, 29, "style"),
        ("herbal-tea-discovery", "Herbal Tea Discovery Box", "A curated set of caffeine-free botanical blends with compostable sachets.", "beauty-wellness", 22.00, 20, 4.8, 91, "wellness"),
        ("creative-sketch-kit", "Creative Sketch Starter Kit", "A portable sketchbook with graphite pencils, blending tools, and a simple guide.", "books-stationery", 29.00, 17, 4.7, 58, "books"),
        ("compact-yoga-mat", "Compact Everyday Yoga Mat", "A grippy, easy-roll mat with alignment marks and a recycled-fabric carry strap.", "sports-outdoors", 44.00, 13, 4.6, 49, "outdoors"),
        ("aster-5g-smartphone", "Aster 5G Smartphone", "A bright 6.4-inch display, all-day battery, and an easy-to-use camera for daily moments.", "mobiles", 329.00, 12, 4.7, 28, "tech"),
        ("northstar-14-laptop", "Northstar 14-inch Laptop", "A slim everyday laptop with a crisp display, responsive keyboard, and long battery life.", "laptops", 649.00, 8, 4.6, 19, "tech"),
        ("everyday-court-sneakers", "Everyday Court Sneakers", "Lightweight lace-up sneakers with a cushioned footbed and flexible everyday sole.", "shoes", 54.00, 21, 4.6, 42, "style"),
        ("tempo-smartwatch", "Tempo Mesh Wristwatch", "A clean analog dial and lightweight mesh band make this a comfortable everyday accessory.", "accessories", 89.00, 15, 4.5, 33, "tech"),
        ("arcade-wireless-controller", "Arcade Wireless Game Controller", "An ergonomic wireless controller with responsive buttons and a rechargeable battery.", "gaming", 49.00, 17, 4.7, 61, "tech"),
        ("brew-one-electric-kettle", "Brew One Electric Kettle", "A compact stainless kettle with a clear water window and automatic shut-off.", "appliances", 42.00, 10, 4.6, 24, "home"),
    ]
    added = 0
    for slug, name, description, category_slug, price, stock, rating, reviews, image_key in products:
        existing = db.scalar(select(Product).where(Product.slug == slug))
        if existing is not None:
            if existing.merchant_id == merchant.id and not existing.image_path:
                existing.image_path = f"/static/images/products/{slug}.jpg"
            continue
        db.add(Product(
            merchant_id=merchant.id,
            category_id=category_map[category_slug].id,
            sku=f"PP-{slug[:12].upper().replace('-', '')}-{uuid4().hex[:4].upper()}",
            name=name,
            slug=slug,
            description=description,
            price=money(price),
            currency="USD",
            stock_quantity=stock,
            image_key=image_key,
            image_path=f"/static/images/products/{slug}.jpg",
            rating=Decimal(str(rating)),
            review_count=reviews,
            is_active=True,
        ))
        added += 1
    db.commit()
    return added


def list_categories(db: Session) -> list[dict]:
    rows = db.scalars(select(ProductCategory).order_by(ProductCategory.sort_order, ProductCategory.name))
    return [category_out(row) for row in rows]


def search_products(db: Session, payload: ProductSearch) -> dict:
    query = (
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.merchant))
        .where(Product.is_active.is_(True))
    )
    if payload.search:
        term = f"%{payload.search.strip()}%"
        query = query.where(Product.name.ilike(term) | Product.description.ilike(term) | Product.sku.ilike(term))
    if payload.category:
        query = query.join(ProductCategory).where(ProductCategory.slug == payload.category)
    if payload.min_price is not None:
        query = query.where(Product.price >= payload.min_price)
    if payload.max_price is not None:
        query = query.where(Product.price <= payload.max_price)
    ordering = {
        "featured": (Product.rating.desc(), Product.review_count.desc(), Product.name.asc()),
        "price_asc": (Product.price.asc(), Product.name.asc()),
        "price_desc": (Product.price.desc(), Product.name.asc()),
        "rating": (Product.rating.desc(), Product.review_count.desc()),
        "newest": (Product.created_at.desc(), Product.id.desc()),
    }[payload.sort]
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.order_by(*ordering).offset((payload.page - 1) * payload.page_size).limit(payload.page_size))
    return {
        "items": [product_out(row) for row in rows],
        "total": total,
        "page": payload.page,
        "page_size": payload.page_size,
        "pages": (total + payload.page_size - 1) // payload.page_size,
    }


def get_product(db: Session, slug: str) -> Product:
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.merchant))
        .where(Product.slug == slug, Product.is_active.is_(True))
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def get_cart(db: Session, user_id: int) -> ShoppingCart | None:
    return db.scalar(
        select(ShoppingCart)
        .options(selectinload(ShoppingCart.items).selectinload(ShoppingCartItem.product).selectinload(Product.category))
        .where(ShoppingCart.user_id == user_id)
    )


def _cart_lines(cart: ShoppingCart) -> list[dict]:
    lines = []
    for item in cart.items:
        product = item.product
        unit_price = money(product.price)
        lines.append({
            "cart_item_id": item.id,
            "product_id": product.id,
            "slug": product.slug,
            "name": product.name,
            "sku": product.sku,
            "category": product.category.name,
            "image_key": product.image_key,
            "image_url": product_image_url(product),
            "quantity": item.quantity,
            "unit_price": unit_price,
            "line_total": money(unit_price * item.quantity),
            "stock_quantity": product.stock_quantity,
            "available": product.is_active and product.stock_quantity >= item.quantity,
        })
    return lines


def _totals(lines: list[dict]) -> dict:
    settings = get_settings()
    subtotal = money(sum((line["line_total"] for line in lines), ZERO))
    tax_rate = Decimal(str(settings.marketplace_tax_rate))
    tax = money(subtotal * tax_rate)
    threshold = money(settings.marketplace_free_shipping_threshold)
    shipping = ZERO if subtotal == ZERO or subtotal >= threshold else money(settings.marketplace_shipping_flat)
    total = money(subtotal + tax + shipping)
    return {
        "currency": "USD",
        "subtotal": subtotal,
        "tax_rate": tax_rate,
        "tax": tax,
        "shipping": shipping,
        "shipping_threshold": threshold,
        "amount_until_free_shipping": money(max(ZERO, threshold - subtotal)),
        "total": total,
    }


def cart_summary(db: Session, user_id: int) -> dict:
    cart = get_cart(db, user_id)
    lines = _cart_lines(cart) if cart else []
    totals = _totals(lines)
    return {
        "items": [{**line, "unit_price": float(line["unit_price"]), "line_total": float(line["line_total"])} for line in lines],
        "item_count": sum(line["quantity"] for line in lines),
        "subtotal": float(totals["subtotal"]),
        "tax_rate": float(totals["tax_rate"]),
        "tax": float(totals["tax"]),
        "shipping": float(totals["shipping"]),
        "shipping_threshold": float(totals["shipping_threshold"]),
        "amount_until_free_shipping": float(totals["amount_until_free_shipping"]),
        "total": float(totals["total"]),
        "currency": totals["currency"],
        "can_checkout": bool(lines) and all(line["available"] for line in lines),
    }


def add_to_cart(db: Session, user: MarketplaceUser, product_id: int, quantity: int) -> dict:
    product = db.scalar(select(Product).where(Product.id == product_id, Product.is_active.is_(True)))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    cart = db.scalar(select(ShoppingCart).where(ShoppingCart.user_id == user.id))
    if cart is None:
        cart = ShoppingCart(user_id=user.id)
        db.add(cart)
        db.flush()
    item = db.scalar(select(ShoppingCartItem).where(ShoppingCartItem.cart_id == cart.id, ShoppingCartItem.product_id == product.id))
    requested = quantity + (item.quantity if item else 0)
    if requested > product.stock_quantity:
        raise HTTPException(status_code=409, detail=f"Only {product.stock_quantity} item(s) are currently in stock")
    if item:
        item.quantity = requested
    else:
        db.add(ShoppingCartItem(cart_id=cart.id, product_id=product.id, quantity=quantity))
    db.commit()
    return cart_summary(db, user.id)


def update_cart_item(db: Session, user: MarketplaceUser, item_id: int, quantity: int) -> dict:
    item = db.scalar(
        select(ShoppingCartItem)
        .join(ShoppingCart)
        .options(selectinload(ShoppingCartItem.product))
        .where(ShoppingCartItem.id == item_id, ShoppingCart.user_id == user.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    if not item.product.is_active:
        raise HTTPException(status_code=409, detail="This product is no longer available")
    if quantity > item.product.stock_quantity:
        raise HTTPException(status_code=409, detail=f"Only {item.product.stock_quantity} item(s) are currently in stock")
    item.quantity = quantity
    db.commit()
    return cart_summary(db, user.id)


def remove_cart_item(db: Session, user: MarketplaceUser, item_id: int) -> dict:
    item = db.scalar(
        select(ShoppingCartItem)
        .join(ShoppingCart)
        .where(ShoppingCartItem.id == item_id, ShoppingCart.user_id == user.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    db.delete(item)
    db.commit()
    return cart_summary(db, user.id)


def validate_cart(db: Session, user_id: int) -> tuple[ShoppingCart, list[dict], dict]:
    cart = get_cart(db, user_id)
    if cart is None or not cart.items:
        raise HTTPException(status_code=400, detail="Your cart is empty")
    lines = _cart_lines(cart)
    if any(not line["available"] for line in lines):
        raise HTTPException(status_code=409, detail="One or more cart items are out of stock or no longer available. Update your cart and try again.")
    return cart, lines, _totals(lines)


def create_marketplace_order(db: Session, user_id: int) -> MarketplaceOrder:
    active_order = db.scalar(
        select(MarketplaceOrder.id).where(
            MarketplaceOrder.user_id == user_id,
            MarketplaceOrder.status == "pending_payment",
            MarketplaceOrder.expires_at > datetime.now(timezone.utc),
        )
    )
    if active_order is not None:
        raise HTTPException(status_code=409, detail="You already have a Sandbox checkout in progress. Complete or cancel it before starting another.")
    cart, lines, totals = validate_cart(db, user_id)
    settings = get_settings()
    order = MarketplaceOrder(
        order_number=f"PP-{uuid4().hex[:12].upper()}",
        user_id=user_id,
        status="pending_payment",
        subtotal=totals["subtotal"],
        tax_amount=totals["tax"],
        shipping_amount=totals["shipping"],
        total_amount=totals["total"],
        tax_rate=totals["tax_rate"],
        currency="USD",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.marketplace_order_ttl_minutes),
    )
    db.add(order)
    db.flush()
    for line in lines:
        product_id = line["product_id"]
        quantity = line["quantity"]
        result = db.execute(
            update(Product)
            .where(Product.id == product_id, Product.is_active.is_(True), Product.stock_quantity >= quantity)
            .values(stock_quantity=Product.stock_quantity - quantity)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Stock changed while preparing checkout. Refresh your cart and try again.")
        db.add(MarketplaceOrderItem(
            order_id=order.id,
            product_id=product_id,
            product_name=line["name"],
            sku=line["sku"],
            unit_price=line["unit_price"],
            quantity=quantity,
            line_total=line["line_total"],
            image_path=line.get("image_url"),
        ))
    db.flush()
    return order


def release_reserved_stock(db: Session, order: MarketplaceOrder) -> None:
    for item in order.items:
        if item.product_id is not None:
            db.execute(update(Product).where(Product.id == item.product_id).values(stock_quantity=Product.stock_quantity + item.quantity))


def expire_stale_orders(db: Session) -> int:
    now = datetime.now(timezone.utc)
    orders = list(db.scalars(
        select(MarketplaceOrder)
        .options(selectinload(MarketplaceOrder.items))
        .where(MarketplaceOrder.status == "pending_payment", MarketplaceOrder.expires_at <= now)
    ))
    for order in orders:
        release_reserved_stock(db, order)
        order.status = "expired"
        paypal = db.scalar(select(PayPalSandboxOrder).where(PayPalSandboxOrder.marketplace_order_id == order.id))
        if paypal:
            paypal.status = "expired"
    if orders:
        db.commit()
    return len(orders)


def order_out(order: MarketplaceOrder) -> dict:
    return {
        "order_number": order.order_number,
        "status": order.status,
        "subtotal": float(order.subtotal),
        "tax": float(order.tax_amount),
        "tax_rate": float(order.tax_rate),
        "shipping": float(order.shipping_amount),
        "total": float(order.total_amount),
        "currency": order.currency,
        "paypal_order_id": order.paypal_order_id,
        "transaction_id": order.transaction_id,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "items": [
            {
                "product_name": item.product_name,
                "sku": item.sku,
                "unit_price": float(item.unit_price),
                "quantity": item.quantity,
                "line_total": float(item.line_total),
                "image_url": product_image_url(item.product, item.image_path),
            }
            for item in order.items
        ],
    }


def merchant_products(db: Session, merchant_id: int) -> list[dict]:
    rows = db.scalars(
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.merchant))
        .where(Product.merchant_id == merchant_id)
        .order_by(Product.created_at.desc())
    )
    return [product_out(row) for row in rows]


def create_merchant_product(db: Session, merchant: MarketplaceUser, payload: ProductCreate) -> dict:
    category = db.scalar(select(ProductCategory).where(ProductCategory.slug == payload.category_slug))
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    slug_base = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")[:120] or "product"
    slug = f"{slug_base}-{uuid4().hex[:6]}"
    product = Product(
        merchant_id=merchant.id,
        category_id=category.id,
        sku=f"PP-{uuid4().hex[:12].upper()}",
        name=payload.name,
        slug=slug,
        description=payload.description,
        price=money(payload.price),
        currency="USD",
        stock_quantity=payload.stock_quantity,
        image_key=_category_key(category.slug),
        rating=Decimal("5.0"),
        review_count=0,
        is_active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_out(get_product(db, product.slug))


def update_merchant_product(db: Session, merchant: MarketplaceUser, product_id: int, payload: ProductUpdate) -> dict:
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.merchant))
        .where(Product.id == product_id, Product.merchant_id == merchant.id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, money(value) if field == "price" else value)
    db.commit()
    db.refresh(product)
    return product_out(product)
