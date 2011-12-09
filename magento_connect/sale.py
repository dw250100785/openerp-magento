# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (c) 2011 Zikzakmedia S.L. (http://zikzakmedia.com) All Rights Reserved.
#                       Raimon Esteve <resteve@zikzakmedia.com>
#    $Id$
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from osv import osv, fields
from tools.translate import _

import netsvc
import time
import mimetypes
import os
import urllib, urllib2
import binascii

from magento import *
from urllib2 import Request, urlopen, URLError, HTTPError

LOGGER = netsvc.Logger()
PRODUCT_TYPE_OUT_ORDER_LINE = ['configurable','bundle']

class sale_shop(osv.osv):
    _inherit = "sale.shop"

    _columns = {
        'magento_shop': fields.boolean('Magento Shop', readonly=True),
        'magento_website': fields.many2one('magento.website', 'Magento Website'),
        'magento_scheduler': fields.boolean('Scheduler', help='Available this Sale Shop crons (import/export)'),
        'magento_tax_include': fields.boolean('Tax Include'),
        'magento_status': fields.one2many('magento.sale.shop.status.type', 'shop_id', 'Status'),
        'magento_payments': fields.one2many('magento.sale.shop.payment.type', 'shop_id', 'Payment'),
        'magento_default_language': fields.many2one('res.lang', 'Language Default', help='Default language this shop. If not select, use lang user'),
        'magento_sale_price': fields.selection([('saleprice','Sale Price'),('pricelist','Pricelist')], 'Price'),
        'magento_sale_stock': fields.selection([('realstock','Real Stock'),('virtualstock','Virtual Stock')], 'Stock'),
        'magento_last_export_products': fields.datetime('Last Export Products', help='This date is last export. If you need export new products, you can modify this date (filter)'),
        'magento_last_export_prices': fields.datetime('Last Export Prices', help='This date is last export. If you need export all product prices, empty this field (long sync)'),
        'magento_last_export_stock': fields.datetime('Last Export Stock', help='This date is last export. If you need export all product prices, empty this field (long sync)'),
        'magento_last_export_images': fields.datetime('Last Export Image', help='This date correspond to the last export. If you need export all images, left empty this field.'),
        'magento_last_export_partners': fields.datetime('Last Export Partners', help='This date correspond to the last export. If you need export all partners, left empty this field.'),
        'magento_last_import_sale_orders': fields.datetime('Last Import Status Sale Orders', help='This date correspond to the last export. If you need export all status, left empty this field.'),
        'magento_from_sale_orders': fields.datetime('From Orders', help='This date is last import. If you need import news orders, you can modify this date (filter)'),
        'magento_to_sale_orders': fields.datetime('To Orders', help='This date is to import (filter)'),
        'magento_last_export_status_orders': fields.datetime('Last Status Orders', help='This date correspond to the last export. If you need export all orders, left empty this field.'),
        'magento_default_picking_policy': fields.selection([('direct', 'Partial Delivery'), ('one', 'Complete Delivery')], 'Packing Policy'),
        'magento_default_order_policy': fields.selection([
            ('prepaid', 'Payment Before Delivery'),
            ('manual', 'Shipping & Manual Invoice'),
            ('postpaid', 'Invoice on Order After Delivery'),
            ('picking', 'Invoice from the Packing'),
        ], 'Shipping Policy'),
        'magento_default_invoice_quantity': fields.selection([('order', 'Ordered Quantities'), ('procurement', 'Shipped Quantities')], 'Invoice on'),
        'magento_status_paid': fields.char('Paid', size=128, help='Status for paid orders (invoice)'),
        'magento_notify_paid': fields.boolean('Notify Paid', size=128, help='Magento notification'),
        'magento_status_delivered': fields.char('Delivered', size=128, help='Status for delivered (picking)'),
        'magento_notify_delivered': fields.boolean('Notify Delivered', size=128, help='Magento notification'),
        'magento_status_paid_delivered': fields.char('Paid/Delivered', size=128, help='Status for paid and delivered'),
        'magento_notify_paid_delivered': fields.boolean('Notify Paid/Delivered', size=128, help='Magento notification'),
    }

    _defaults = {
        'magento_sale_price': 'saleprice',
        'magento_sale_stock': 'virtualstock',
        'magento_from_sale_orders': lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    def magento_export_products(self, cr, uid, ids, context=None):
        """
        Sync Products to Magento Site filterd by magento_sale_shop
        Get ids all products and send one to one to Magento
        :return True
        """

        product_shop_ids = []
        for shop in self.browse(cr, uid, ids):
            magento_app = shop.magento_website.magento_app_id
            last_exported_time = shop.magento_last_export_products

            # write sale shop date last export
            self.pool.get('sale.shop').write(cr, uid, shop.id, {'magento_last_export_products': time.strftime('%Y-%m-%d %H:%M:%S')})

            product_product_ids = self.pool.get('product.product').search(cr, uid, [('magento_exportable','=',True),('magento_sale_shop','in',shop.id)])

            for product_product in self.pool.get('product.product').perm_read(cr, uid, product_product_ids):
                # product.product create/modify > date exported last time
                if last_exported_time < product_product['create_date'][:19] or (product_product['write_date'] and last_exported_time < product_product['write_date'][:19]):
                    product_shop_ids.append(product_product['id'])

            if shop.magento_default_language:
                context['lang'] = shop.magento_default_language.code

            LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "Products to sync: %s" % (product_shop_ids))

            context['shop'] = shop
            self.magento_export_products_stepbystep(cr, uid, magento_app, product_shop_ids, context)

        return True

    def magento_export_products_stepbystep(self, cr, uid, magento_app, ids, context=None):
        """
        Get all IDs products to create/write to Magento
        Use Base External Mapping to transform values
        Get values and call magento is step by step (product by product)
        :return mgn_id
        """
        if len(ids) == 0:
            return True

        context['magento_app'] = magento_app

        with Product(magento_app.uri, magento_app.username, magento_app.password) as product_api:
            for product in self.pool.get('product.product').browse(cr, uid, ids, context):
                context['product_id'] = product.id
                product_product_vals = self.pool.get('base.external.mapping').get_oerp_to_external(cr, uid, 'magento.product.product',[product.id], context)
                product_template_vals = self.pool.get('base.external.mapping').get_oerp_to_external(cr, uid, 'magento.product.template',[product.product_tmpl_id.id], context)

                values = dict(product_product_vals[0], **product_template_vals[0])
                print values
                mapping_id = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'product.product', product.id)

                # get dicc values
                product_sku = values['sku']
                product_type = values['type']
                product_attribute_set = values['set']

                # remove dicc values
                del values['id']
                del values['sku']
                del values['type']
                del values['set']

                if mapping_id: #uptate
                    mappings = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [mapping_id])
                    product_mgn_id = mappings[0]['mgn_id']

                    store_view = None
                    if 'store_view' in context:
                        store_view = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'magento.storeview', context['store_view'].id)
                        store_view = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [store_view])
                        store_view = store_view[0]['mgn_id']

                    #~ print product_sku, values
                    product_api.update(product_mgn_id, values, store_view)
                    LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "Update Product SKU %s. OpenERP ID %s, Magento ID %s" % (product_sku, product.id, product_mgn_id))
                else: #create
                    #~ print product_type, product_attribute_set, product_sku, values
                    product_mgn_id = product_api.create(product_type, product_attribute_set, product_sku, values)
                    LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "Create Product: %s. OpenERP ID %s, Magento ID %s" % (product_sku, product.id, product_mgn_id))
                    self.pool.get('magento.external.referential').create_external_referential(cr, uid, magento_app, 'product.product', product.id, product_mgn_id)

        LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "End Products Export")

        return product_mgn_id

    def magento_export_prices(self, cr, uid, ids, context=None):
        """
        Sync Products Price to Magento Site
        Get price products when last export time and send one to one to Magento
        :return True
        """

        decimal = self.pool.get('decimal.precision').precision_get(cr, uid, 'Sale Price')

        product_shop_ids = []
        for shop in self.browse(cr, uid, ids):
            magento_app = shop.magento_website.magento_app_id
            last_exported_time = shop.magento_last_export_prices

            # write sale shop date last export
            self.write(cr, uid, shop.id, {'magento_last_export_prices': time.strftime('%Y-%m-%d %H:%M:%S')})

            product_product_ids = self.pool.get('product.product').search(cr, uid, [('magento_exportable','=',True),('magento_sale_shop','in',shop.id)])

            for product_product in self.pool.get('product.product').perm_read(cr, uid, product_product_ids):
                # product.product create/modify > date exported last time
                if last_exported_time < product_product['create_date'][:19] or (product_product['write_date'] and last_exported_time < product_product['write_date'][:19]):
                    product_shop_ids.append(product_product['id'])

            with Product(magento_app.uri, magento_app.username, magento_app.password) as product_api:
                context['shop'] = shop
                for product in self.pool.get('product.product').browse(cr, uid, product_shop_ids, context):
                    mgn_id = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'product.product', product.id)
                    if mgn_id:
                        mgn_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [mgn_id])[0]['mgn_id']
                    #~ store_view = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'magento.storeview', shop.id)
                    #~ store_view  = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [store_view])[0]['mgn_id']

                    price = ''
                    if not mgn_id:#not product created/exist in Magento. Create
                        mgn_id = self.magento_export_products_stepbystep(cr, uid, magento_app, ids, context)

                    if shop.magento_sale_price == 'pricelist' and shop.pricelist_id:
                        price = self.pool.get('product.pricelist').price_get(cr, uid, [shop.pricelist_id.id], product.id, 1.0)[shop.pricelist_id.id]
                    else:
                        price = product.product_tmpl_id.list_price

                    if shop.magento_tax_include:
                        price_compute_all = self.pool.get('account.tax').compute_all(cr, uid, product.product_tmpl_id.taxes_id, price, 1, address_id=None, product=product.product_tmpl_id, partner=None)
                        price = price_compute_all['total_included']

                    if price:
                        price = '%.*f' % (decimal, price) #decimal precision

                    data = {'price':price}
                    #~ product_mgn_id = product_api.update(mgn_id, data, store_view)
                    product_mgn_id = product_api.update(mgn_id, data)

                    LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "Update Product Prices: %s. OpenERP ID %s, Magento ID %s" % (price, product.id, mgn_id))

        LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "End Product Prices Export")

        return True

    def magento_export_stock(self, cr, uid, ids, context=None):
        """
        Sync Products Stock to Magento Site
        Get stock all products and send one to one to Magento
        :return True
        """

        product_shop_ids = []
        for shop in self.browse(cr, uid, ids):
            magento_app = shop.magento_website.magento_app_id
            stock_id = shop.warehouse_id.lot_stock_id.id

            product_shop_ids = self.pool.get('product.product').search(cr, uid, [('magento_exportable','=',True),('magento_sale_shop','in',shop.id)])

            # base_sale_multichannels
            if shop.magento_last_export_stock:
                recent_move_ids = self.pool.get('stock.move').search(cr, uid, [('write_date', '>', shop.magento_last_export_stock),('product_id', 'in', product_shop_ids),('state', '!=', 'draft'),('state', '!=', 'cancel')])
            else:
                recent_move_ids = self.pool.get('stock.move').search(cr, uid, [('product_id', 'in', product_shop_ids),('state', '!=', 'draft'),('state', '!=', 'cancel')])
            product_ids = [move.product_id.id for move in self.pool.get('stock.move').browse(cr, uid, recent_move_ids) if move.product_id.state != 'obsolete']
            product_ids = [x for x in set(product_ids)]

            # write sale shop date last export
            self.write(cr, uid, shop.id, {'magento_last_export_stock': time.strftime('%Y-%m-%d %H:%M:%S')})

            with Inventory(magento_app.uri, magento_app.username, magento_app.password) as inventory_api:
                context['shop'] = shop
                for product in self.pool.get('product.product').browse(cr, uid, product_ids, context):
                    stock = 0
                    mgn_id = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'product.product', product.id)
                    if mgn_id:
                        mgn_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [mgn_id])[0]['mgn_id']

                    if not mgn_id:#not product created/exist in Magento. Create
                        mgn_id = self.magento_export_products_stepbystep(cr, uid, magento_app, ids, context)

                    """Calculate Stock from real stock or virtual Stock"""
                    if shop.magento_sale_price == 'realstock':
                        stock = self.pool.get('product.product').read(cr, uid, product.id, ['qty_available'], {'location': stock_id})['qty_available']
                    else:
                        stock = self.pool.get('product.product').read(cr, uid, product.id, ['virtual_available'], {'location': stock_id})['virtual_available']

                    """Is in Stock"""
                    is_in_stock = int(stock > 0) or False

                    data = {'qty':stock, 'is_in_stock':is_in_stock}
                    inventory_api.update(mgn_id, data)

                    LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "Update Product Stock: %s. OpenERP ID %s, Magento ID %s" % (stock, product.id, mgn_id))

            self.write(cr, uid, [context['shop'].id], {'magento_last_export_stock': time.strftime('%Y-%m-%d %H:%M:%S')})

        LOGGER.notifyChannel('Magento Sale Shop', netsvc.LOG_INFO, "End Product Stock Export")

        return True

    def magento_export_images(self, cr, uid, ids, context=None):
        """
        Sync Images to Magento Site filterd by magento_sale_shop
        Get ids all product images and send one to one to Magento
        :return True
        """

        magento_external_referential_obj = self.pool.get('magento.external.referential')
        product_product_obj = self.pool.get('product.product')

        magento_product_images_ids = []
        for shop in self.browse(cr, uid, ids):
            magento_app = shop.magento_website.magento_app_id
            last_exported_time = shop.magento_last_export_images

            # write sale shop date last export
            self.write(cr, uid, shop.id, {'magento_last_export_images': time.strftime('%Y-%m-%d %H:%M:%S')})

            product_images_magento_app_ids = self.pool.get('product.images.magento.app').search(cr, uid, [('magento_app_id','=',magento_app.id)])

            product_images_ids = []
            for product_image in self.pool.get('product.images.magento.app').read(cr, uid, product_images_magento_app_ids, ['product_images_id']):
                product_images_ids.append(product_image['product_images_id'][0])

            for product_image in self.pool.get('product.images').perm_read(cr, uid, product_images_ids):
                # product.product create/modify > date exported last time
                if last_exported_time < product_image['create_date'][:19] or (product_image['write_date'] and last_exported_time < product_image['write_date'][:19]):
                    magento_product_images_ids.append(product_image['id'])

            with ProductImages(magento_app.uri, magento_app.username, magento_app.password) as product_image_api:
                for product_image in self.pool.get('product.images').browse(cr, uid, magento_product_images_ids):
                    is_last_exported = self.pool.get('product.images.magento.app').search(cr, uid, [('magento_app_id','=',magento_app.id),('product_images_id','=',product_image.id),('magento_exported','=',True)])

                    product = self.pool.get('magento.external.referential').check_oerp2mgn(cr, uid, magento_app, 'product.product', product_image.product_id.id)
                    if product:
                        product = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [product])
                        product = product[0]['mgn_id']
                    else:
                        LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Skip! Product not exists. Not create Image ID %s" % (product_image.id))
                        continue

                    image_name = product_image.name

                    types = []
                    if product_image.magento_base_image:
                        types.append('image')
                    if product_image.magento_small_image:
                        types.append('small_image')
                    if product_image.magento_thumbnail:
                        types.append('thumbnail')

                    data = {
                        'label': product_image.name,
                        'position': product_image.magento_position,
                        'exclude': product_image.magento_exclude,
                        'types': types,
                    }

                    if len(is_last_exported)>0:
                        mgn_file_name = product_image.magento_filename
                        try:
                            product_image_api.update(product, mgn_file_name, data)
                            LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Update Image %s, Product Mgn ID %s" % (product_image.name, product))
                        except:
                            LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Error Update Image %s, Product Mgn ID %s" % (product_image.name, product))

                    else:
                        """
                        if Product Image Link
                            if product_image_repository installed
                            not product image filename
                        else Product Image Filename
                        """
                        image = False
                        if product_image.link:
                            product_images_repository = self.pool.get('ir.module.module').search(cr, uid, [('name','=','product_images_repository'),('state','=','installed')])
                            if len(product_images_repository)>0:
                                user = self.pool.get('res.users').browse(cr, uid, uid)
                                company = user.company_id
                                try:
                                    (filename, header) = urllib.urlretrieve(os.path.join(company.local_media_repository, product_image.filename))
                                    image_mime = filename and mimetypes.guess_type(filename)[0] or 'image/jpeg'
                                    img = open(filename , 'rb')
                                    image = img.read()
                                except:
                                    LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Skip! Not exist %s/%s" % (company.local_media_repository, product_image.filename))

                            if not image:
                                url = product_image.filename
                                try:
                                    image_mime = product_image.filename and mimetypes.guess_type(product_image.filename)[0] or 'image/jpeg'
                                    img = urllib2.urlopen(url)
                                    image = img.read()
                                except:
                                    LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Skip! Not exist %s" % (url))
                                    continue
                        else:
                            image_mime = product_image.image and mimetypes.guess_type(product_image.image)[0] or 'image/jpeg'
                            image = product_image.image
                            image = binascii.a2b_base64(image)

                        try:
                            mgn_file_name = product_image_api.create(product, image, image_name, image_mime)
                            product_image_api.update(product, mgn_file_name, data)
                            LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Create Image %s, Product Mgn ID %s" % (product_image.name, product))
                            #update magento filename
                            self.pool.get('product.images').write(cr,uid,[product_image.id],{'magento_filename':mgn_file_name})
                            # update magento_exported
                            prod_images_mgn_apps = self.pool.get('product.images.magento.app').search(cr, uid, [('product_images_id','=',product_image.id),('magento_app_id','=',magento_app.id)])
                            if len(prod_images_mgn_apps)>0:
                                self.pool.get('product.images.magento.app').write(cr,uid,prod_images_mgn_apps,{'magento_exported':True})
                        except:
                            LOGGER.notifyChannel('Magento Sync Product Image', netsvc.LOG_INFO, "Error Create Image %s, Product Mgn ID %s" % (product_image.name, product))

        return True

    def magento_import_orders(self, cr, uid, ids, context=None):
        """
        Sync Orders Magento to OpenERP filterd by magento_sale_shop
        Get ids all sale.order and send one to one to Magento
        :return True
        """

        for sale_shop in self.browse(cr, uid, ids):
            magento_app = sale_shop.magento_website.magento_app_id

            with Order(magento_app.uri, magento_app.username, magento_app.password) as order_api:
                creted_filter = {'from':sale_shop.magento_from_sale_orders}
                if sale_shop.magento_to_sale_orders:
                    creted_filter['to'] = sale_shop.magento_to_sale_orders

                ofilter = {'created_at':creted_filter}
                orders = order_api.list(ofilter)

                #~ Update date last import
                date_from_import = sale_shop.magento_to_sale_orders and sale_shop.magento_to_sale_orders or time.strftime('%Y-%m-%d %H:%M:%S')
                self.write(cr, uid, ids, {'magento_from_sale_orders': date_from_import,'magento_to_sale_orders':False})

                for order in orders:
                    order_id = order['order_id']
                    code = order['increment_id']

                    mgn_order_mapping = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'sale.order', order_id)

                    if mgn_order_mapping:
                        LOGGER.notifyChannel('Magento Sync Sale Order', netsvc.LOG_ERROR, "Skip! magento %s, order %s, mapping id %s. Not create" % (magento_app.name, code, mgn_order_mapping))
                        continue
                    values = order_api.info(code)
                    sale_order_id = self.pool.get('sale.order').magento_create_order(cr, uid, sale_shop, values, context)
                    cr.commit()
                if not orders:
                    LOGGER.notifyChannel('Magento Sync Sale Order', netsvc.LOG_INFO, "Not Orders available, magento %s, date > %s" % (magento_app.name, creted_filter))

        return True

    def magento_export_status(self, cr, uid, ids, context=None):
        """
        Sync Sale orders to Magento Site filterd by magento_sale_shop
        Get ids all sale.order and send one to one to Magento
        :return True
        """

        status = False
        comment = False
        notify = False
        sale_order_ids = []

        for shop in self.browse(cr, uid, ids):
            magento_app = shop.magento_website.magento_app_id
            last_exported_time = shop.magento_last_export_status_orders

            LOGGER.notifyChannel('Magento Sync Sale Order Status', netsvc.LOG_INFO, "magento %s, sale shop %s" % (magento_app.name, shop.id))

            # write sale shop date last export
            self.pool.get('sale.shop').write(cr, uid, shop.id, {'magento_last_export_status_orders': time.strftime('%Y-%m-%d %H:%M:%S')})
            sale_order_ids = self.pool.get('sale.order').search(cr, uid, [('shop_id','=',shop.id)])
            
            for sale_order in self.pool.get('sale.order').perm_read(cr, uid, sale_order_ids):
                # product.product modify > date exported last time
                if  sale_order['write_date'] and last_exported_time < sale_order['write_date'][:19]:
                    sale_order_ids.append(sale_order['id'])
            sale_order_ids = [x for x in set(sale_order_ids)]

            for sale_order in self.pool.get('sale.order').browse(cr, uid, sale_order_ids):
                if sale_order.invoiced:
                    notify = shop.magento_notify_paid
                    status = shop.magento_status_paid
                if sale_order.shipped:
                    notify = shop.magento_notify_delivered
                    status = shop.magento_status_delivered
                if sale_order.invoiced and sale_order.shipped:
                    notify = shop.magento_notify_paid_delivered
                    status = shop.magento_status_paid_delivered

                #not update status if status not change
                if status == sale_order.magento_status:
                    status = False

                if status:
                    with Order(magento_app.uri, magento_app.username, magento_app.password) as order_api:
                        order_api.addcomment(sale_order.name, status, comment, notify)
                    self.pool.get('sale.order').write(cr, uid, [sale_order.id], {'magento_status': status})
                    LOGGER.notifyChannel('Order Status', netsvc.LOG_INFO, "%s, status: %s" % (sale_order.name, status))

        return True

    def _sale_shop(self, cr, uid, callback, context=None):
        """
        Sale Shop Magento available Scheduler
        :return True
        """
        if context is None:
            context = {}

        ids = self.pool.get('sale.shop').search(cr, uid, [('magento_shop', '=', True), ('magento_scheduler', '=', True)], context=context)
        if ids:
            callback(cr, uid, ids, context=context)

        return True

    def run_export_catalog_scheduler(self, cr, uid, context=None):
        """Scheduler Catalog Product Cron"""
        self._sale_shop(cr, uid, self.magento_export_products, context=context)

    def run_export_price_scheduler(self, cr, uid, context=None):
        """Scheduler Catalog Price Cron"""
        self._sale_shop(cr, uid, self.magento_export_prices, context=context)

    def run_export_stock_scheduler(self, cr, uid, context=None):
        """Scheduler Catalog Stock Cron"""
        self._sale_shop(cr, uid, self.magento_export_stock, context=context)

    def run_import_orders_scheduler(self, cr, uid, context=None):
        """Scheduler Orders Status Cron"""
        self._sale_shop(cr, uid, self.magento_import_orders, context=context)

    def run_update_orders_scheduler(self, cr, uid, context=None):
        """Scheduler Orders Status Cron"""
        self._sale_shop(cr, uid, self.magento_export_status, context=context)

sale_shop()

class sale_order(osv.osv):
    _inherit = "sale.order"

    _columns = {
        'magento_status': fields.char('Status', size=128, readonly=True, help='Magento Status'),
        'magento_gift_message': fields.text('Gift Message'),
    }

    def magento_create_order(self, cr, uid, sale_shop, values, context=None):
        """
        Create Magento Order
        Not use Base External Mapping
        :sale_shop: object
        :values: dicc order
        :return sale_order_id (OpenERP ID)
        """

        vals = {}
        confirm = False
        cancel = False
        customer_info = True
        magento_app = sale_shop.magento_website.magento_app_id

        """Partner OpenERP.
        If not, create partner
        """
        customer_id = values['customer_id'] and values['customer_id'] or values['billing_address']['customer_id']
        partner_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner', customer_id)
        if not partner_mapping_id:
            customer_info = False
            customer  = self.pool.get('res.partner').magento_customer_info(magento_app, customer_id)
            partner_id = self.pool.get('res.partner').magento_create_partner(cr, uid, magento_app, customer, context)
            magento_app_customer_ids = self.pool.get('magento.app.customer').magento_app_customer_create(cr, uid, magento_app, partner_id, customer, context)
            partner_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner', customer_id)
        partner_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [partner_mapping_id])[0]['oerp_id']
        customer_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [partner_mapping_id])[0]['mgn_id']

        """Partner Address Invoice OpenERP.
        If not, create partner address
        """
        billing_address = None
        if 'customer_address_id' in values['billing_address']:
            billing_address = values['billing_address']['customer_address_id']
        # If Create Partner same time create order, Magento Customer Address ID = 0
        if billing_address == '0' or billing_address == None:
            partner_address_invoice_id = self.pool.get('res.partner.address').magento_ghost_customer_address(cr, uid, magento_app, partner_id, customer_id, values['billing_address'])
        else:
            partner_invoice_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner.address', billing_address)
            if not partner_invoice_mapping_id:
                if customer_info:
                    customer_info = False
                    customer  = self.pool.get('res.partner').magento_customer_info(magento_app, customer_id)
                with CustomerAddress(magento_app.uri, magento_app.username, magento_app.password) as customer_address_api:
                    customer_address = customer_address_api.info(billing_address)
                    if not 'customer_address_id' in customer_address:
                        customer_address['customer_address_id'] = billing_address
                    customer_address['email'] = customer['email']
                self.pool.get('res.partner.address').magento_create_partner_address(cr, uid, magento_app, partner_id, customer_address)
                partner_invoice_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner.address', billing_address)
            partner_address_invoice_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [partner_invoice_mapping_id])[0]['oerp_id']

        """Partner Address Delivery OpenERP.
        If not, create partner address
        """
        shipping_address = None
        if 'customer_address_id' in values['shipping_address']:
            shipping_address = values['shipping_address']['customer_address_id']
        # If Create Partner same time create order, Magento Customer Address ID = 0
        if shipping_address == '0' or shipping_address == None:
            partner_address_shipping_id = self.pool.get('res.partner.address').magento_ghost_customer_address(cr, uid, magento_app, partner_id, customer_id, values['shipping_address'])
        else:
            partner_shipping_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner.address', shipping_address)
            if not partner_shipping_mapping_id:
                if customer_info:
                    customer_info = False
                    customer  = self.pool.get('res.partner').magento_customer_info(magento_app, customer_id)
                with CustomerAddress(magento_app.uri, magento_app.username, magento_app.password) as customer_address_api:
                    customer_address = customer_address_api.info(shipping_address)
                    if not 'customer_address_id' in customer_address:
                        customer_address['customer_address_id'] = shipping_address
                    customer_address['email'] = customer['email']
                self.pool.get('res.partner.address').magento_create_partner_address(cr, uid, magento_app, partner_id, customer_address)
                partner_shipping_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'res.partner.address', shipping_address)
            partner_address_shipping_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [partner_shipping_mapping_id])[0]['oerp_id']

        partner = self.pool.get('res.partner').browse(cr, uid, partner_id, context)

        """Payment Type"""
        if 'method' in values['payment']:
            payment_types = self.pool.get('magento.sale.shop.payment.type').search(cr, uid,
                    [('method','=',values['payment']['method']),('shop_id','=',sale_shop.id)]
                )
            if len(payment_types)>0:
                payment_type = self.pool.get('magento.sale.shop.payment.type').read(cr, uid, payment_types, ['payment_type_id'])
                vals['payment_type'] = payment_type[0]['payment_type_id'][0]

        """Sale Order"""
        vals['shop_id'] = sale_shop.id
        vals['name'] = values['increment_id']
        vals['date_order'] = values['created_at'][:10]
        vals['partner_id'] = partner_id
        vals['partner_invoice_id'] = partner_address_invoice_id
        vals['partner_shipping_id'] = partner_address_shipping_id
        vals['partner_order_id'] = partner_address_invoice_id
        vals['pricelist_id'] = partner.property_product_pricelist.id
        if 'customer_note' in values:
            vals['note'] = values['customer_note']
        vals['origin'] = "%s-%s" % (magento_app.name,values['increment_id'])
        if 'gift_message' in values:
            vals['magento_gift_message'] = values['gift_message']

        vals['order_policy'] = sale_shop.magento_default_order_policy
        vals['picking_policy'] = sale_shop.magento_default_picking_policy
        vals['invoice_quantity'] = sale_shop.magento_default_invoice_quantity

        """Magento Status Order"""
        magento_status = values['status_history'][0]['status']
        vals['magento_status'] = magento_status
        mgn_status = self.pool.get('magento.sale.shop.status.type').search(cr, uid, [
                ('status','=',magento_status),
                ('shop_id','=',sale_shop.id),
            ])

        if len(mgn_status)>0:
            mgn_status = self.pool.get('magento.sale.shop.status.type').browse(cr, uid, mgn_status[0])
            vals['order_policy'] = mgn_status.order_policy
            vals['picking_policy'] = mgn_status.picking_policy
            vals['invoice_quantity'] = mgn_status.invoice_quantity
            if mgn_status.confirm:
                confirm = True
            if mgn_status.cancel:
                cancel = True

        sale_order_id = self.create(cr, uid, vals, context)
        sale_order = self.browse(cr, uid, sale_order_id)

        """Sale Order Discount"""
        if values['discount_amount'] != '0.0000':
            sale_order_delivery = self.pool.get('sale.order.line').magento_create_discount_line(cr, uid, magento_app, sale_order, values, context)

        """Sale Order Delivery"""
        sale_order_delivery = self.pool.get('sale.order.line').magento_create_delivery_line(cr, uid, magento_app, sale_order, values, context)

        """Sale Order Line"""
        for item in values['items']:
            if item['product_type'] not in PRODUCT_TYPE_OUT_ORDER_LINE:
                sale_order_line = self.pool.get('sale.order.line').magento_create_order_line(cr, uid, magento_app, sale_order, item, context)
            
        """Confirm Order - Change status sale order"""
        if confirm:
            LOGGER.notifyChannel('Magento Sync Sale Order', netsvc.LOG_INFO, "Order %s change status: Done" % (sale_order_id))
            netsvc.LocalService("workflow").trg_validate(uid, 'sale.order', sale_order_id, 'order_confirm', cr)

        """Cancel Order - Change status sale order"""
        if cancel:
            LOGGER.notifyChannel('Magento Sync Sale Order', netsvc.LOG_INFO, "Order %s change status: Cancel" % (sale_order_id))
            netsvc.LocalService("workflow").trg_validate(uid, 'sale.order', sale_order_id, 'cancel', cr)

        """Magento APP Customer
        Add last store - history stores buy
        """
        self.pool.get('magento.app.customer').magento_last_store(cr, uid, magento_app, partner, values)

        """Mapping Sale Order"""
        self.pool.get('magento.external.referential').create_external_referential(cr, uid, magento_app, 'sale.order', sale_order.id, values['order_id'])

        LOGGER.notifyChannel('Magento Sync Sale Order', netsvc.LOG_INFO, "Order %s, magento %s, openerp id %s, magento id %s" % (values['increment_id'], magento_app.name, sale_order.id, values['order_id']))

        cr.commit()

        return sale_order.id

sale_order()

class sale_order_line(osv.osv):
    _inherit = "sale.order.line"

    _columns = {
        'magento_gift_message': fields.text('Gift Message'),
    }

    def magento_create_order_line(self, cr, uid, magento_app, sale_order, item={}, context=None):
        """
        Create Magento Order Line
        Not use Base External Mapping
        :magento_app: object
        :sale_order: object
        :item: dicc order line Magento
        :return sale_order_line_id (OpenERP ID)
        """

        vals_line = {}

        decimals = self.pool.get('decimal.precision').precision_get(cr, uid, 'Sale Price')
        
        #Default values
        product_id = False
        product_uom = magento_app.product_uom_id.id
        product_uom_qty = round(float(item['qty_ordered']),decimals)
        weight = item['weight'] and item['weight'] or 0
        weight = round(float(weight),decimals)

        product_mapping_id = self.pool.get('magento.external.referential').check_mgn2oerp(cr, uid, magento_app, 'product.product', item['product_id'])
        if product_mapping_id:
            """Product is mapping. Get Product OpenERP"""
            product_id = self.pool.get('magento.external.referential').get_external_referential(cr, uid, [product_mapping_id])[0]['oerp_id']
            product = self.pool.get('product.product').browse(cr, uid, product_id)
            product_uom = product.uos_id.id and product.uos_id.id or product.uom_id.id
            product_id_change = self.pool.get('sale.order.line').product_id_change(cr, uid,
                [sale_order.id], sale_order.partner_id.property_product_pricelist.id, product.id,
                product_uom_qty, product_uom, partner_id=sale_order.partner_id.id)

            vals_line['delay'] = product_id_change['value']['delay']
            weight = product_id_change['value']['th_weight']
            vals_line['type'] = product_id_change['value']['type']
            tax_ids = [self.pool.get('account.tax').browse(cr, uid, t_id).id for t_id in product_id_change['value']['tax_id']]
            vals_line['tax_id'] = [(6, 0, tax_ids)]

        vals_line['order_id'] = sale_order.id
        vals_line['product_id'] = product_id
        vals_line['name'] = item['name']
        vals_line['price_unit'] = float(item['price'])
        vals_line['product_uom_qty'] = product_uom_qty
        vals_line['product_uom'] = product_uom
        vals_line['notes'] = item['description']
        vals_line['th_weight'] = weight
        if 'gift_message' in item:
            vals_line['magento_gift_message'] = item['gift_message']
        sale_order_line_id = self.create(cr, uid, vals_line, context)

        return sale_order_line_id

    def magento_create_delivery_line(self, cr, uid, magento_app, sale_order, values=False, context=None):
        """
        Create Magento Order Line Delivery
        Not use Base External Mapping
        :magento_app: object
        :sale_order: object
        :item: dicc order Magento
        :return sale_order_line_id (OpenERP ID)
        """
        if not values:
            return False

        delivery_product = magento_app.product_delivery_default_id
        delivery_ids = self.pool.get('delivery.carrier').search(cr, uid, [('name','=',values['shipping_description'])])
        if len(delivery_ids)>0:
            delivery = self.pool.get('delivery.carrier').browse(cr, uid, delivery_ids[0], context)
            delivery_product = delivery.product_id

        item = {
            'product_id': delivery_product.id,
            'qty_ordered': 1,
            'weight': delivery_product.weight and delivery_product.weight or 0,
            'name': delivery_product.name,
            'price': values['base_shipping_amount'],
            'description': '',
        }
        sale_order_line_id = self.magento_create_order_line(cr, uid, magento_app, sale_order, item, context)

        return sale_order_line_id

    def magento_create_discount_line(self, cr, uid, magento_app, sale_order, values=False, context=None):
        """
        Create Magento Order Line Discount
        Not use Base External Mapping
        :magento_app: object
        :sale_order: object
        :values: dicc order Magento
        :return sale_order_line_id (OpenERP ID)
        """
        if not values:
            return False

        discount_product = magento_app.product_discount_default_id

        item = {
            'product_id': discount_product.id,
            'qty_ordered': 1,
            'weight': discount_product.weight and discount_product.weight or 0,
            'name': discount_product.name,
            'price': values['discount_amount'],
            'description': '',
        }
        sale_order_line_id = self.magento_create_order_line(cr, uid, magento_app, sale_order, item, context)

        return sale_order_line_id

sale_order_line()

class magento_sale_shop_status_type(osv.osv):
    _name = "magento.sale.shop.status.type"

    _description = "Magento Sale Shop Status Type"
    _rec_name = "status"

    _columns = {
        'status': fields.char('Status', size=255, required=True, help='Code Status (example, cancel, pending, processing,..)'),
        'shop_id': fields.many2one('sale.shop','Shop', required=True),
        'picking_policy': fields.selection([('direct', 'Partial Delivery'), ('one', 'Complete Delivery')], 'Packing Policy'),
        'order_policy': fields.selection([
         ('prepaid', 'Payment Before Delivery'),
         ('manual', 'Shipping & Manual Invoice'),
         ('postpaid', 'Invoice on Order After Delivery'),
         ('picking', 'Invoice from the Packing'),
        ], 'Shipping Policy'),
        'invoice_quantity': fields.selection([('order', 'Ordered Quantities'), ('procurement', 'Shipped Quantities')], 'Invoice on'),
        'confirm': fields.boolean('Confirm', help="Confirm order. Sale Order change state draft to done, and generate picking and/or invoice automatlly"),
        'cancel': fields.boolean('Cancel', help="Cancel order. Sale Order change state draft to cancel"),
     }

magento_sale_shop_status_type()

class magento_sale_shop_payment_type(osv.osv):
    _name = "magento.sale.shop.payment.type"

    _description = "Magento Sale Shop Payment Type"
    _rec_name = "status"

    _columns = {
        'method': fields.char('Method', size=255, required=True, help='Code Payment (example, paypal, checkmo, ccsave,...'),
        'shop_id': fields.many2one('sale.shop','Shop', required=True),
        'payment_type_id': fields.many2one('payment.type','Payment Type', required=True),
    }

magento_sale_shop_payment_type()
