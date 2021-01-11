# An template for using L476 Discovery board as USB Virtual COM device

CubeMX defaultly generated codes, the only thing to change is usb_conf.h where the following code should be modified as :
/** Alias for memory allocation. */
#define USBD_malloc         (void *)USBD_static_malloc

/** Alias for memory release. */
#define USBD_free          USBD_static_free

This is due to lack of heap size which causing USB init failed in usbd_cdc (with an error mark in device manager)

Solution:
Use above static memory allocation or increase heap to 0x400
